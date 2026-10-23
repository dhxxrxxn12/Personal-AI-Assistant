"""Local, durable reminder storage and delivery scheduling.

Reminders are notifications only.  They never contain an action to execute.
The store uses an atomic replace and a per-occurrence delivery ledger so a
restart or a second scheduler tick cannot deliver the same occurrence twice.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from core.runtime_config import runtime_path

DEFAULT_PATH = runtime_path("reminders.json", Path.home() / ".jarvis" / "reminders.json")
_LOCK = RLock()


def local_zone_name() -> str:
    return datetime.now().astimezone().tzinfo.key if hasattr(datetime.now().astimezone().tzinfo, "key") else "UTC"


def _zone(name: Optional[str]):
    value = (name or local_zone_name()).strip()
    try:
        return ZoneInfo(value)
    except Exception as exc:
        raise ValueError(f"Unknown timezone '{value}'.") from exc


def _aware(value: datetime, zone) -> datetime:
    return (value.replace(tzinfo=zone) if value.tzinfo is None else value).astimezone(zone)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_clock(text: str) -> Optional[tuple[int, int]]:
    match = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*", text.lower())
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if minute > 59:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif hour > 23:
        return None
    return hour, minute


@dataclass(frozen=True)
class ResolvedReminder:
    message: str
    due_at: datetime
    recurrence: Optional[str] = None


def resolve_request(text: str, *, now: Optional[datetime] = None, timezone_name: Optional[str] = None) -> ResolvedReminder:
    """Resolve the small, explicit reminder language understood locally.

    A missing meridiem for a 1-12 hour clock is rejected as ambiguous.  The
    voice model may supply normalized date/time fields directly to the action.
    """
    raw = " ".join(str(text or "").split())
    match = re.search(r"\b(?:at|@)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", raw, re.I)
    if not match:
        raise ValueError("Please include a time such as 8 PM or 20:00.")
    clock_text = match.group(1)
    if re.fullmatch(r"\d{1,2}(?::\d{2})?", clock_text) and int(clock_text.split(":")[0]) <= 12:
        raise ValueError("Is that time AM or PM?")
    clock = _parse_clock(clock_text)
    if clock is None:
        raise ValueError("That time is invalid.")
    zone = _zone(timezone_name)
    current = _aware(now or datetime.now(zone), zone)
    recurrence = "daily" if re.search(r"\bevery\s+day\b|\bdaily\b", raw, re.I) else None
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", raw)
    if date_match:
        try:
            day = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("That date is invalid.") from exc
    elif re.search(r"\btomorrow\b", raw, re.I):
        day = (current + timedelta(days=1)).date()
    else:
        day = current.date()
    due = datetime(day.year, day.month, day.day, clock[0], clock[1], tzinfo=zone)
    if recurrence is None and due <= current:
        raise ValueError("That time has already passed. Please choose a future time.")
    if recurrence == "daily" and due <= current:
        due += timedelta(days=1)
    message = re.sub(r"\bremind\s+me\b", "", raw, flags=re.I)
    message = re.sub(r"\b(?:at|@)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", message, flags=re.I)
    message = re.sub(r"\b(?:tomorrow|every\s+day|daily|on\s+\d{4}-\d{2}-\d{2})\b", "", message, flags=re.I).strip(" .,;")
    if message.lower().startswith("to "):
        message = message[3:].strip()
    if not message:
        raise ValueError("What should I remind you about?")
    return ResolvedReminder(message=message[:380], due_at=due, recurrence=recurrence)


class ReminderStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "reminders": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("reminders"), dict):
                return data
        except (OSError, ValueError):
            pass
        raise OSError("Reminder storage is unreadable or corrupt.")

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".reminders-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def list(self, include_finished: bool = False) -> list[dict]:
        with _LOCK:
            records = list(self._read()["reminders"].values())
        if not include_finished:
            records = [r for r in records if r.get("status") in {"scheduled", "delivered"}]
        return sorted(records, key=lambda r: r.get("next_due", ""))

    def create(self, resolved: ResolvedReminder) -> dict:
        record = {"id": uuid.uuid4().hex, "message": resolved.message, "next_due": _iso(resolved.due_at),
                  "timezone": str(resolved.due_at.tzinfo), "recurrence": resolved.recurrence,
                  "status": "scheduled", "delivered_occurrences": [], "created_at": _iso(datetime.now(timezone.utc))}
        with _LOCK:
            data = self._read(); data["reminders"][record["id"]] = record; self._write(data)
        return record

    def mutate(self, reminder_id: str, operation: str, *, due_at: Optional[datetime] = None) -> dict:
        with _LOCK:
            data = self._read(); record = data["reminders"].get(reminder_id)
            if not record:
                raise KeyError("Reminder not found.")
            if operation == "complete": record["status"] = "completed"
            elif operation == "cancel": record["status"] = "cancelled"
            elif operation == "reschedule":
                if due_at is None: raise ValueError("A new future time is required.")
                record.update(next_due=_iso(due_at), timezone=str(due_at.tzinfo), status="scheduled")
            else: raise ValueError("Unsupported reminder operation.")
            self._write(data)
            return record

    def claim_due(self, now: datetime) -> list[dict]:
        claimed = []
        with _LOCK:
            data = self._read()
            for record in data["reminders"].values():
                if record.get("status") != "scheduled": continue
                zone = _zone(record.get("timezone")); due = datetime.fromisoformat(record["next_due"]).astimezone(zone)
                current = _aware(now, zone)
                if due > current: continue
                occurrence = record["next_due"]
                if occurrence in record.setdefault("delivered_occurrences", []): continue
                record["delivered_occurrences"].append(occurrence)
                if record.get("recurrence") == "daily":
                    next_due = due + timedelta(days=1)
                    # A restart may span several occurrences. Notify once for
                    # the overdue reminder, then move past all missed slots so
                    # the user is not hit by a burst of stale notifications.
                    while next_due <= current:
                        next_due += timedelta(days=1)
                    record["next_due"] = _iso(next_due)
                else: record["status"] = "delivered"
                claimed.append(dict(record, occurrence=occurrence, delivered_message=record["message"]))
            if claimed: self._write(data)
        return claimed


class ReminderService:
    def __init__(self, store: Optional[ReminderStore] = None, deliver: Optional[Callable[[dict], None]] = None):
        self.store = store or ReminderStore()
        self.deliver = deliver or (lambda record: None)

    def tick(self, now: Optional[datetime] = None) -> list[dict]:
        delivered = self.store.claim_due(now or datetime.now(timezone.utc))
        for record in delivered:
            try: self.deliver(record)
            except Exception: pass
        return delivered
