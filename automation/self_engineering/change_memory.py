"""Strictly keyed engineering-memory records, separate from task workflows."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from core.runtime_config import BASE_DIR, runtime_path


@dataclass
class EngineeringMemoryRecord:
    goal: str
    capabilities: List[str]
    subsystem: str
    actions: List[str] = field(default_factory=list)
    changes: List[str] = field(default_factory=list)
    tests: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    repairs: List[str] = field(default_factory=list)
    successful_solution: str = ""
    deployment_result: str = ""
    timestamp: float = field(default_factory=time.time)


class ChangeMemory:
    """Persist engineering outcomes and match only on exact context keys."""

    def __init__(self, path: Optional[Path] = None, *, max_records: int = 100) -> None:
        self.path = Path(path or runtime_path("engineering_memory.json", BASE_DIR / "automation" / "engineering_memory.json"))
        self.max_records = max_records
        self.records: List[EngineeringMemoryRecord] = []
        self._load()

    @staticmethod
    def _redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: ("<redacted>" if re.search(r"key|token|secret|password", str(key), re.I) else ChangeMemory._redact(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [ChangeMemory._redact(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", value)
        return value

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, list):
            return
        for item in raw[-self.max_records :]:
            if isinstance(item, dict) and item.get("goal") and item.get("subsystem"):
                self.records.append(EngineeringMemoryRecord(**{
                    key: item[key] for key in EngineeringMemoryRecord.__dataclass_fields__ if key in item
                }))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [self._redact(asdict(item)) for item in self.records[-self.max_records :]]
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def record(self, entry: EngineeringMemoryRecord) -> EngineeringMemoryRecord:
        self.records.append(entry)
        self.records = self.records[-self.max_records :]
        self._save()
        return entry

    @staticmethod
    def _key(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9_]+", value.lower()))

    def find_similar(
        self,
        *,
        goal: str,
        capability: str,
        subsystem: str,
        action: Optional[str] = None,
    ) -> List[EngineeringMemoryRecord]:
        matches = []
        goal_key = self._key(goal)
        capability_key = self._key(capability)
        subsystem_key = self._key(subsystem)
        action_key = self._key(action) if action else None
        for record in reversed(self.records):
            if self._key(record.goal) != goal_key:
                continue
            if capability_key not in {self._key(item) for item in record.capabilities}:
                continue
            if self._key(record.subsystem) != subsystem_key:
                continue
            if action_key and action_key not in {self._key(item) for item in record.actions}:
                continue
            matches.append(record)
        return matches
