"""Explicit-command sandbox build/deploy wrapper."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .policies import EngineeringOperation, EngineeringPolicy
from .test_runner import TestResult


@dataclass(frozen=True)
class SandboxConfig:
    root: Path
    build_command: Sequence[str] = field(default_factory=tuple)
    deploy_command: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class SandboxHandle:
    sandbox_id: str
    path: Path
    branch: str
    worktree: bool = True

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "sandbox_id": self.sandbox_id,
            "path": str(self.path),
            "branch": self.branch,
            "worktree": self.worktree,
        }


class SandboxManager:
    """Run only preconfigured, shell-free commands in a separate directory."""

    ALLOWED_PROGRAMS = {
        "python", "python3", "pytest", "git", "ruff", "mypy", "compileall",
    }

    def __init__(self, workspace_root: Path, config: SandboxConfig, policy: EngineeringPolicy) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.config = config
        self.policy = policy
        self.sandbox_root = Path(config.root).resolve()
        if self.sandbox_root == self.workspace_root:
            raise ValueError("Sandbox root must be isolated from the production workspace.")
        if not self.sandbox_root.exists():
            self.sandbox_root.mkdir(parents=True, exist_ok=True)
        if not self.sandbox_root.is_dir():
            raise NotADirectoryError(str(self.sandbox_root))

    @classmethod
    def for_repository(
        cls,
        workspace_root: Path,
        policy: EngineeringPolicy,
        *,
        workspace_dir: Optional[Path] = None,
    ) -> "SandboxManager":
        """Create a manager for isolated Git worktrees in one repository."""
        root = Path(workspace_dir or (Path(workspace_root) / ".jarvis-workspaces"))
        return cls(Path(workspace_root), SandboxConfig(root), policy)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.workspace_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def create_worktree(self, sandbox_id: str, *, approved: bool = False) -> SandboxHandle:
        """Create one unique, branch-backed worktree inside the repository."""
        value = str(sandbox_id).strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,63}", value):
            raise ValueError("Sandbox id contains unsafe path characters.")
        parent = self.sandbox_root
        parent.mkdir(parents=True, exist_ok=True)
        target = (parent / value).resolve()
        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("Sandbox path is outside the repository boundary.") from exc
        if target.exists():
            raise FileExistsError(str(target))
        self.policy.require(EngineeringOperation.CREATE_BRANCH, target, approved=approved)
        # Use a flat ref name. Some local Git layouts already contain a
        # conflicting `refs/heads/jarvis` path, which makes a nested
        # `jarvis/engineering/...` ref impossible to create.
        branch = f"jarvis-engineering-{value}"
        result = self._git("worktree", "add", "-b", branch, str(target), "HEAD")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Git worktree creation failed.")
        return SandboxHandle(value, target, branch)

    def policy_for(self, handle: SandboxHandle) -> EngineeringPolicy:
        resolved = Path(handle.path).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("Sandbox is outside the repository boundary.") from exc
        return EngineeringPolicy(resolved, self.policy.permission_manager)

    def cleanup(self, handle: SandboxHandle, *, preserve: bool = False) -> None:
        """Remove the worktree only when its evidence is no longer needed."""
        resolved = Path(handle.path).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("Sandbox is outside the repository boundary.") from exc
        if preserve:
            return
        result = self._git("worktree", "remove", "--force", str(resolved))
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Git worktree cleanup failed.")

    def _run(self, command: Sequence[str]) -> TestResult:
        if not command:
            raise ValueError("Sandbox command is not configured.")
        forbidden = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}
        program = Path(str(command[0])).name.lower()
        if program in forbidden or program not in self.ALLOWED_PROGRAMS or "-c" in command:
            raise ValueError("Sandbox commands may not invoke a shell interpreter.")
        if any(str(argument) in {";", "&&", "||", "|", ">", ">>"} for argument in command[1:]):
            raise ValueError("Sandbox command contains an unsafe shell operator.")
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
