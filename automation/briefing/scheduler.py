"""Deterministic, timezone-aware briefing scheduling.

There is no background loop here: the scheduler answers pure questions about a
persisted definition and an explicit ``now``. A host decides when to call
``tick``, which keeps behaviour reproducible and restart-safe. JARVIS already
registers one-shot reminders with the operating system scheduler
(``actions/reminder.py``); this module complements that mechanism with an
in-process durable briefing schedule instead of duplicating it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .models import BriefingDefinition, MissedRunPolicy, parse_time_of_day

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]


class BriefingScheduler:
    """Schedule maths for one briefing definition; never starts a thread."""

    MAX_CATCH_UP_DAYS = 7
    MAX_MISSED_SLOTS = 32

    def __init__(self, definition: BriefingDefinition) -> None:
        self.definition = definition.validate()

    def zone(self):
        if self.definition.timezone.upper() == "UTC":
            return timezone.utc
        if ZoneInfo is None:
            raise RuntimeError("zoneinfo is unavailable; use UTC or install tzdata")
        return ZoneInfo(self.definition.timezone)

    def local(self, now: datetime) -> datetime:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(self.zone())

    def slot_for(self, day: datetime) -> datetime:
        hour, minute = parse_time_of_day(self.definition.time_of_day)
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def next_run(self, now: datetime) -> Optional[datetime]:
        """The next scheduled slot strictly after ``now`` (None when disabled)."""
        if not self.definition.enabled:
            return None
        current = self.local(now)
        candidate = self.slot_for(current)
        if candidate <= current:
            candidate = self.slot_for(current + timedelta(days=1))
        return candidate

    def previous_slot(self, now: datetime) -> Optional[datetime]:
        """The most recent scheduled slot at or before ``now``."""
        if not self.definition.enabled:
            return None
        current = self.local(now)
        candidate = self.slot_for(current)
        if candidate > current:
            candidate = self.slot_for(current - timedelta(days=1))
        return candidate

    def grace(self) -> timedelta:
        return timedelta(minutes=int(self.definition.grace_minutes))

    def due_run(self, now: datetime, *, last_run: Optional[datetime] = None) -> Optional[datetime]:
        """Return the slot that should execute now, if any.

        The most recent slot is due while it is inside the grace window, and a
        slot that already ran (``last_run``) is never returned again. A missed
        slot outside the grace window is deliberately skipped so a restart never
        replays a stale briefing.
        """
        slot = self.previous_slot(now)
        if slot is None:
            return None
        if self.local(now) - slot > self.grace():
            return None
        if last_run is not None:
            comparison = last_run if last_run.tzinfo else last_run.replace(tzinfo=timezone.utc)
            if comparison.astimezone(self.zone()) >= slot:
                return None
        return slot

    def is_due(self, now: datetime, *, last_run: Optional[datetime] = None) -> bool:
        return self.due_run(now, last_run=last_run) is not None

    def missed_runs(
        self,
        now: datetime,
        *,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[datetime]:
        """Slots strictly after ``since`` up to ``now``, oldest first, bounded."""
        if not self.definition.enabled:
            return []
        current = self.local(now)
        newest = self.previous_slot(current)
        if newest is None:
            return []
        bound = int(limit or self.MAX_MISSED_SLOTS)
        oldest_allowed = newest - timedelta(days=self.MAX_CATCH_UP_DAYS)
        if since is not None:
            comparison = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            start = max(comparison.astimezone(self.zone()), oldest_allowed)
        else:
            start = oldest_allowed
        slots: List[datetime] = []
        day = newest
        while day > start and len(slots) < bound:
            slots.append(day)
            day = self.slot_for(day - timedelta(days=1))
        return sorted(slots)

    def describe(self, now: datetime, *, last_run: Optional[datetime] = None) -> dict:
        next_run = self.next_run(now)
        due = self.due_run(now, last_run=last_run)
        return {
            "definition_id": self.definition.definition_id,
            "enabled": bool(self.definition.enabled),
            "time_of_day": self.definition.time_of_day,
            "timezone": self.definition.timezone,
            "local_now": self.local(now).isoformat(),
            "next_run": next_run.isoformat() if next_run else None,
            "due_now": due.isoformat() if due else None,
            "grace_minutes": int(self.definition.grace_minutes),
            "missed_run_policy": self.definition.missed_run_policy,
            "policy": MissedRunPolicy(self.definition.missed_run_policy).value,
        }
