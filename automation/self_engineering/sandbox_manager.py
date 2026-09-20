"""Explicit-command sandbox build/deploy wrapper."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .policies import EngineeringOperation, EngineeringPolicy
from .test_runner import TestResult


@dataclass(frozen=True)
class SandboxConfig:
    root: Path
    build_command: Sequence[str]
    deploy_command: Sequence[str]


class SandboxManager:
    """Run only preconfigured, shell-free commands in a separate directory."""

    def __init__(self, workspace_root: Path, config: SandboxConfig, policy: EngineeringPolicy) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.config = config
        self.policy = policy
        self.sandbox_root = Path(config.root).resolve()
        if self.sandbox_root == self.workspace_root:
            raise ValueError("Sandbox root must be isolated from the production workspace.")
        if not self.sandbox_root.is_dir():
            raise NotADirectoryError(str(self.sandbox_root))
        if not config.build_command or not config.deploy_command:
            raise ValueError("Sandbox build and deploy commands must be configured.")

    def _run(self, command: Sequence[str]) -> TestResult:
        forbidden = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}
        if command[0] in forbidden or "-c" in command:
            raise ValueError("Sandbox commands may not invoke a shell interpreter.")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GEMINI_API_KEY", "JARVIS_ADMIN_TOKEN", "JARVIS_AGENT_TOKEN", "JARVIS_CLOUD_TOKEN"}
        }
        try:
            result = subprocess.run(
                list(command),
                cwd=self.sandbox_root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return TestResult(list(command), result.returncode, result.stdout, result.stderr, 0.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return TestResult(list(command), 124, "", f"Sandbox command failed: {type(exc).__name__}: {exc}", 0.0)

    def build(self, *, approved: bool = False) -> TestResult:
        self.policy.require(EngineeringOperation.SANDBOX_DEPLOY, approved=approved)
        return self._run(self.config.build_command)

    def deploy(self, *, approved: bool = False) -> TestResult:
        self.policy.require(EngineeringOperation.SANDBOX_DEPLOY, approved=approved)
        return self._run(self.config.deploy_command)
