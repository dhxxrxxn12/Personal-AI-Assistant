"""cloud/uploads.py — file drop storage for the JARVIS command center.

The command center lets the operator drop a file onto the HUD.  The Cloud
server stores it (same folder convention as the local dashboard so both
interfaces see the same files) and the browser can then create a normal task
referencing that path; the Mac agent is what actually processes it.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from cloud.settings import settings

BASE_DIR = Path(__file__).resolve().parent.parent

MAX_UPLOAD_MB = 500

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


def make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    if settings.data_dir is not None:
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        return settings.uploads_dir
    for candidate in (
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    fallback = BASE_DIR / "uploads"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


UPLOADS_DIR = make_uploads_dir()


def safe_filename(name: str) -> str:
    """Strip directories and unsafe characters from a client-supplied name."""
    cleaned = _SAFE_NAME.sub("_", Path(str(name or "")).name).strip() or "upload"
    return cleaned[:120]


def unique_path(name: str) -> Path:
    """Avoid clobbering an existing upload with the same name."""
    safe = safe_filename(name)
    candidate = UPLOADS_DIR / safe
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(1, 1000):
        candidate = UPLOADS_DIR / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    return UPLOADS_DIR / f"{stem}_{int(time.time())}{suffix}"


def list_uploads(limit: int = 25) -> List[Dict[str, object]]:
    """Most recently modified uploads first."""
    try:
        files = [path for path in UPLOADS_DIR.iterdir() if path.is_file()]
    except Exception:
        return []

    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    listed: List[Dict[str, object]] = []
    for path in files[: max(0, int(limit))]:
        try:
            stat = path.stat()
        except OSError:
            continue
        listed.append({
            "name": path.name,
            "size": stat.st_size,
            "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
            "path": str(path),
        })
    return listed


def resolve_upload(name: str) -> Optional[Path]:
    """Resolve a client-supplied name inside the uploads folder only."""
    safe = safe_filename(name)
    candidate = (UPLOADS_DIR / safe).resolve()
    try:
        candidate.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None
