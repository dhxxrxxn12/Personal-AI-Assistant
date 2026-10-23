"""Read-only diagnostic capability for the self-engineering subsystem."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


JARVIS_VERSION = "1.0.0"


@dataclass(frozen=True)
class DiagnosticReport:
    version: str
    git_commit: Optional[str]
    test_status: Dict[str, Any]
    active_action_count: int
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "git_commit": self.git_commit,
            "test_status": dict(self.test_status),
            "active_action_count": self.active_action_count,
            "generated_at": self.generated_at,
        }


def _git_commit(root: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def collect_diagnostics(
    root: Path,
    *,
    action_count: Optional[int] = None,
    test_status: Optional[Dict[str, Any]] = None,
) -> DiagnosticReport:
    """Collect only bounded version, Git, test, and action-count facts."""
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise NotADirectoryError(str(root))
    count = int(action_count) if action_count is not None else 0
    status = dict(test_status or {"status": "not_run"})
    return DiagnosticReport(
        version=JARVIS_VERSION,
        git_commit=_git_commit(resolved),
        test_status=status,
        active_action_count=max(0, count),
    )
