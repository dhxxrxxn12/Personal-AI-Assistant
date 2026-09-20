"""Shared runtime paths and environment configuration.

The desktop application keeps its historical local paths when no cloud data
directory is configured.  A production Cloud instance sets ``JARVIS_DATA_DIR``
to its mounted persistent volume, which moves runtime state without changing
the task, memory, or permission formats.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def configured_data_dir() -> Path | None:
    raw = os.getenv("JARVIS_DATA_DIR", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def runtime_path(name: str, legacy_path: Path) -> Path:
    """Return a persistent-volume path when configured, else the legacy path."""
    data_dir = configured_data_dir()
    return (data_dir / name) if data_dir else Path(legacy_path)


def runtime_dir(name: str, legacy_path: Path) -> Path:
    """Return a persistent directory while preserving local compatibility."""
    data_dir = configured_data_dir()
    return (data_dir / name) if data_dir else Path(legacy_path)


def migrate_legacy_file(target: Path, legacy: Path) -> None:
    """Copy legacy local state to a configured volume exactly once.

    This is intentionally copy-only: existing volume data always wins and the
    original local file is never removed.
    """
    if (
        target == legacy
        or target.exists()
        or not legacy.exists()
        or os.getenv("JARVIS_SKIP_LEGACY_MIGRATION", "").strip() == "1"
    ):
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, target)
    except OSError:
        # Startup should still report the real storage/configuration error later.
        pass


def env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default
