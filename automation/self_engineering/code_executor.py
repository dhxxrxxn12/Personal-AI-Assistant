"""Controlled text-file changes for the self-engineering subsystem."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .policies import EngineeringOperation, EngineeringPolicy


@dataclass(frozen=True)
class ChangeResult:
    operation: str
    path: str
    before_sha256: Optional[str]
    after_sha256: Optional[str]
    diff: str


class CodeExecutor:
    """Expose only bounded, text-based changes inside the approved workspace."""

    MAX_FILE_BYTES = 1_000_000
    MAX_DIFF_LINES = 2_000

    def __init__(self, policy: EngineeringPolicy) -> None:
        self.policy = policy
        self.operations: list[dict[str, Any]] = []

    def _record(self, operation: str, path: str, *, diff: str = "") -> None:
        self.operations.append({
            "operation": operation,
            "path": path,
            "diff": diff,
        })

    def trace(self) -> list[dict[str, Any]]:
        """Return a copy of the bounded, secret-free operation trace."""
        return [dict(item) for item in self.operations]

    @staticmethod
    def _sha256(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _path(self, path: str | Path, *, must_exist: bool = False) -> Path:
        return self.policy.resolve_path(path, allow_missing=not must_exist)

    def read_source(self, path: str | Path) -> str:
        resolved = self._path(path, must_exist=True)
        self.policy.require(EngineeringOperation.READ_SOURCE, resolved)
        if not resolved.is_file():
            raise IsADirectoryError(str(path))
        if resolved.stat().st_size > self.MAX_FILE_BYTES:
            raise ValueError("Source file exceeds the engineering size limit.")
        content = resolved.read_text(encoding="utf-8")
        self._record(EngineeringOperation.READ_SOURCE.value, self.policy.relative_path(resolved))
        return content

    def _diff(self, relative: str, before: Optional[str], after: Optional[str], *, new_path: Optional[str] = None) -> str:
        old_lines = (before or "").splitlines(keepends=True)
        new_lines = (after or "").splitlines(keepends=True)
        old_name = relative if before is not None else "/dev/null"
        new_name = new_path or (relative if after is not None else "/dev/null")
        diff = list(difflib.unified_diff(old_lines, new_lines, fromfile=old_name, tofile=new_name))
        if not diff:
            raise ValueError("Change would produce an empty diff.")
        if len(diff) > self.MAX_DIFF_LINES:
            raise ValueError("Change diff exceeds the engineering size limit.")
        return "".join(diff)

    def _write(self, path: str | Path, content: str, operation: EngineeringOperation, *, approved: bool) -> ChangeResult:
        if not isinstance(content, str):
            raise TypeError("Engineering changes must be text content.")
        if len(content.encode("utf-8")) > self.MAX_FILE_BYTES:
            raise ValueError("File content exceeds the engineering size limit.")
        resolved = self._path(path)
        self.policy.require(operation, resolved, approved=approved)
        if resolved.exists() and resolved.is_dir():
            raise IsADirectoryError(str(path))
        before = resolved.read_text(encoding="utf-8") if resolved.exists() else None
        diff = self._diff(self.policy.relative_path(resolved), before, content)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        result = ChangeResult(
            operation=operation.value,
            path=self.policy.relative_path(resolved),
            before_sha256=self._sha256(before),
            after_sha256=self._sha256(content),
            diff=diff,
        )
        self._record(result.operation, result.path, diff=result.diff)
        return result

    def create_file(self, path: str | Path, content: str, *, approved: bool = False) -> ChangeResult:
        resolved = self._path(path)
        if resolved.exists():
            raise FileExistsError(str(path))
        return self._write(path, content, EngineeringOperation.CREATE_FILE, approved=approved)

    def modify_file(
        self,
        path: str | Path,
        content: str,
        *,
        expected_sha256: Optional[str] = None,
        approved: bool = False,
    ) -> ChangeResult:
        resolved = self._path(path, must_exist=True)
        if not resolved.is_file():
            raise IsADirectoryError(str(path))
        current = resolved.read_text(encoding="utf-8")
        if expected_sha256 is None:
            raise ValueError("modify_file requires the hash of the inspected version.")
        if self._sha256(current) != expected_sha256:
            raise ValueError("Target changed after inspection; refuse to overwrite it.")
        return self._write(path, content, EngineeringOperation.MODIFY_FILE, approved=approved)

    def rename_file(self, source: str | Path, destination: str | Path, *, approved: bool = False) -> ChangeResult:
        source_path = self._path(source, must_exist=True)
        destination_path = self._path(destination)
        self.policy.require(EngineeringOperation.RENAME_FILE, source_path, approved=approved)
        self.policy.require(EngineeringOperation.RENAME_FILE, destination_path, approved=approved)
        if not source_path.is_file():
            raise IsADirectoryError(str(source))
        if destination_path.exists():
            raise FileExistsError(str(destination))
        before = source_path.read_text(encoding="utf-8")
        source_path.rename(destination_path)
        diff = self._diff(
            self.policy.relative_path(source_path),
            before,
            before,
            new_path=self.policy.relative_path(destination_path),
        )
        result = ChangeResult(
            operation=EngineeringOperation.RENAME_FILE.value,
            path=self.policy.relative_path(destination_path),
            before_sha256=self._sha256(before),
            after_sha256=self._sha256(before),
            diff=diff,
        )
        self._record(result.operation, result.path, diff=result.diff)
        return result

    def delete_file(self, path: str | Path, *, approved: bool = False) -> ChangeResult:
        resolved = self._path(path, must_exist=True)
        self.policy.require(EngineeringOperation.DELETE_FILE, resolved, approved=approved)
        if not resolved.is_file():
            raise IsADirectoryError(str(path))
        before = resolved.read_text(encoding="utf-8")
        diff = self._diff(self.policy.relative_path(resolved), before, None)
        resolved.unlink()
        result = ChangeResult(
            operation=EngineeringOperation.DELETE_FILE.value,
            path=self.policy.relative_path(resolved),
            before_sha256=self._sha256(before),
            after_sha256=None,
            diff=diff,
        )
        self._record(result.operation, result.path, diff=result.diff)
        return result
