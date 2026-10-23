"""Execution ledger: duplicate prevention and restart recovery evidence.

The ledger is the only place that decides whether a scheduled slot already ran,
so the same slot is never delivered twice, even across a JARVIS restart.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from core.runtime_config import BASE_DIR, runtime_path

from .models import BriefingRunRecord

DEFAULT_LEDGER_PATH = runtime_path("briefing_ledger.json", BASE_DIR / "automation" / "briefing_ledger.json")


class BriefingLedger:
    """Append-only local run history with atomic writes and bounded growth."""

    MAX_RECORDS = 200

    def __init__(self, path: Optional[Path] = None, *, max_records: int = MAX_RECORDS) -> None:
        self.path = Path(path or DEFAULT_LEDGER_PATH)
        self.max_records = max(1, int(max_records))

    def records(self) -> List[BriefingRunRecord]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        entries: List[BriefingRunRecord] = []
        for item in raw[-self.max_records :]:
            try:
                entries.append(BriefingRunRecord.from_dict(item))
            except (ValueError, TypeError):
                continue
        return entries

    def record(self, entry: BriefingRunRecord) -> BriefingRunRecord:
        entries = [*self.records(), entry][-self.max_records :]
        payload = [item.to_dict() for item in entries]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)
        return entry

    def has_run(self, definition_id: str, scheduled_for: str) -> bool:
        return any(
            item.definition_id == definition_id and item.scheduled_for == scheduled_for
            for item in self.records()
        )

    def last_record(self, definition_id: str) -> Optional[BriefingRunRecord]:
        for item in reversed(self.records()):
            if item.definition_id == definition_id:
                return item
        return None

    def last_run(self, definition_id: str) -> Optional[datetime]:
        """The most recent *executed* slot, used to suppress duplicates."""
        for item in reversed(self.records()):
            if item.definition_id != definition_id or item.duplicate:
                continue
            try:
                return datetime.fromisoformat(item.scheduled_for)
            except ValueError:
                continue
        return None

    def runs(self, definition_id: Optional[str] = None, *, limit: int = 20) -> List[dict]:
        selected = [
            item.to_dict()
            for item in self.records()
            if definition_id is None or item.definition_id == definition_id
        ]
        return selected[-max(1, int(limit)) :]

    def counts(self, definition_id: Optional[str] = None) -> dict:
        selected = [
            item for item in self.records()
            if definition_id is None or item.definition_id == definition_id
        ]
        return {
            "total": len(selected),
            "delivered": sum(1 for item in selected if item.status == "delivered"),
            "skipped": sum(1 for item in selected if item.status == "skipped"),
            "failed": sum(1 for item in selected if item.status == "failed"),
            "duplicates_prevented": sum(1 for item in selected if item.duplicate),
        }
