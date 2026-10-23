"""Persistent Cloud state helpers for controlled self-engineering tasks."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;]+)"
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "<redacted>"
            if re.search(r"key|token|secret|password", str(key), re.I)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_RE.sub(r"\1=<redacted>", value)
    return value


class EngineeringTaskStore:
    """Small atomic JSON store kept separate from the normal task queue."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            self.tasks = data if isinstance(data, dict) else {}

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(redact(self.tasks), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self.tasks.get(task_id)
            return dict(value) if isinstance(value, dict) else None

    def values(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [dict(value) for value in self.tasks.values() if isinstance(value, dict)]

    def put(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = str(task.get("task_id") or task.get("id") or "").strip()
        if not task_id:
            raise ValueError("Engineering task must have a task_id.")
        with self._lock:
            self.tasks[task_id] = redact(dict(task))
            self.save()
            return dict(self.tasks[task_id])
