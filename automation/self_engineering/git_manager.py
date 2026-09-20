"""Non-destructive Git operations for engineering checkpoints."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable, List

from .policies import EngineeringOperation, EngineeringPolicy


class GitManager:
    """Create explicit branches/checkpoints without push or history rewriting."""

    BRANCH_RE = re.compile(r"^[a-z0-9][a-z0-9/_-]{0,79}$")
    COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

    def __init__(self, root: Path, policy: EngineeringPolicy) -> None:
        self.root = Path(root).resolve()
        self.policy = policy

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def status(self) -> List[str]:
        result = self._run("status", "--short", "--branch")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "git status failed")
        return result.stdout.splitlines()

    def diff(self, *, staged: bool = False) -> str:
        args = ["diff"]
        if staged:
            args.append("--cached")
        result = self._run(*args)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "git diff failed")
        return result.stdout

    def create_branch(self, name: str, *, approved: bool = False) -> str:
        if not self.BRANCH_RE.fullmatch(name) or name in {"main", "master"}:
            raise ValueError("Invalid or protected branch name.")
        self.policy.require(EngineeringOperation.CREATE_BRANCH, approved=approved)
        result = self._run("switch", "-c", name)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "git branch creation failed")
        return name

    def _validate_paths(self, paths: Iterable[str]) -> List[str]:
        values = []
        for path in paths:
            resolved = self.policy.resolve_path(path, allow_missing=True)
            if self.policy.is_protected(resolved):
                raise PermissionError(f"Protected path cannot be checkpointed: {path}")
            if not resolved.exists():
                tracked = self._run("ls-files", "--error-unmatch", "--", self.policy.relative_path(resolved))
                if tracked.returncode:
                    raise FileNotFoundError(str(path))
            values.append(self.policy.relative_path(resolved))
        if not values:
            raise ValueError("A checkpoint must name explicit paths.")
        return values

    def checkpoint(self, message: str, paths: Iterable[str], *, approved: bool = False) -> str:
        if not message.strip() or "\n" in message or "\r" in message:
            raise ValueError("Commit message must be a single non-empty line.")
        values = self._validate_paths(paths)
        self.policy.require(EngineeringOperation.CREATE_COMMIT, approved=approved)
        staged = self._run("add", "--", *values)
        if staged.returncode:
            raise RuntimeError(staged.stderr.strip() or "git add failed")
        commit = self._run("commit", "-m", message.strip())
        if commit.returncode:
            raise RuntimeError(commit.stderr.strip() or "git commit failed")
        sha = self._run("rev-parse", "HEAD")
        if sha.returncode:
            raise RuntimeError(sha.stderr.strip() or "could not resolve checkpoint")
        return sha.stdout.strip()

    def rollback(self, commit: str, *, approved: bool = False) -> str:
        if not self.COMMIT_RE.fullmatch(commit):
            raise ValueError("Rollback accepts only an explicit commit hash.")
        self.policy.require(EngineeringOperation.ROLLBACK, approved=approved)
        result = self._run("revert", "--no-edit", commit)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "git revert failed; resolve manually")
        return self._run("rev-parse", "HEAD").stdout.strip()
