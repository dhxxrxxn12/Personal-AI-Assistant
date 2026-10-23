"""Allowlisted test and static-check execution."""

from __future__ import annotations

# The module name follows the requested architecture, but it is a support
# module rather than a pytest collection target.
__test__ = False

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .policies import EngineeringOperation, EngineeringPolicy


@dataclass(frozen=True)
class TestResult:
    command: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def success(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()

    def to_dict(self) -> dict[str, object]:
        def safe(value: str) -> str:
            return re.sub(
                r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;]+)",
                r"\1=<redacted>",
                value or "",
            )[-12000:]
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": safe(self.stdout),
            "stderr": safe(self.stderr),
            "duration_seconds": round(self.duration_seconds, 3),
            "success": self.success,
        }


TestResult.__test__ = False


class TestRunner:
    """Never invokes a shell and never accepts an arbitrary command string."""

    def __init__(self, root: Path, policy: EngineeringPolicy, *, timeout_seconds: float = 120.0) -> None:
        self.root = Path(root).resolve()
        self.policy = policy
        self.timeout_seconds = timeout_seconds

    def _paths(self, paths: Optional[Iterable[str]]) -> List[str]:
        values = []
        for path in paths or []:
            resolved = self.policy.resolve_path(path, allow_missing=False)
            self.policy.require(EngineeringOperation.READ_SOURCE, resolved)
            values.append(self.policy.relative_path(resolved))
        return values

    def _run(self, command: List[str]) -> TestResult:
        started = time.monotonic()
        # Test processes must not inherit provider credentials or runtime
        # tokens.  The runner never loads .env and does not need those values.
        secret_names = {
            "GEMINI_API_KEY",
            "JARVIS_ADMIN_TOKEN",
            "JARVIS_AGENT_TOKEN",
            "JARVIS_CLOUD_TOKEN",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
        }
        environment = {
            key: value for key, value in os.environ.items()
            if key not in secret_names and not any(
                marker in key.upper() for marker in ("API_KEY", "_TOKEN", "_SECRET")
            )
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            returncode = 124
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + "\nTest command timed out."
        except OSError as exc:
            returncode = 127
            stdout = ""
            stderr = f"Could not start test command: {type(exc).__name__}: {exc}"
        return TestResult(command, returncode, stdout, stderr, time.monotonic() - started)

    def run_tests(self, paths: Optional[Iterable[str]] = None) -> TestResult:
        selected = self._paths(paths)
        return self._run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *selected])

    def run_static_check(self, paths: Optional[Iterable[str]] = None) -> TestResult:
        selected = self._paths(paths)
        if not selected:
            selected = [
                path.relative_to(self.root).as_posix()
                for path in sorted(self.root.iterdir())
                if path.is_dir() and path.name not in {".git", ".venv", "__pycache__"}
            ]
        return self._run([sys.executable, "-m", "compileall", "-q", *selected])


TestRunner.__test__ = False
