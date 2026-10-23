"""Durable, local coordination for safe proactive voice notifications."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable, Optional

from core.privacy import redact_sensitive_text
from core.runtime_config import runtime_path


DEFAULT_PATH = runtime_path(
    "proactive_notifications.json",
    Path.home() / ".jarvis" / "proactive_notifications.json",
)
_LOCK = RLock()
_ACTIVE = {"queued", "deferred", "attempted"}
_TERMINAL = {"delivered", "failed", "dismissed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class NotificationStore:
    """Small atomic JSON ledger for notification lifecycle state."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "events": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("events"), dict):
                return data
        except (OSError, ValueError):
            pass
        raise OSError("Proactive notification storage is unreadable or corrupt.")

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".proactive-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def get(self, event_id: str) -> Optional[dict]:
        with _LOCK:
            return self._read()["events"].get(event_id)

    def list(self, limit: int = 50) -> list[dict]:
        with _LOCK:
            events = list(self._read()["events"].values())
        return sorted(events, key=lambda item: item.get("updated_at", ""), reverse=True)[:max(1, int(limit))]

    def put(self, event: dict) -> dict:
        with _LOCK:
            data = self._read()
            data["events"][event["event_id"]] = event
            self._write(data)
        return event

    def update(self, event_id: str, **changes) -> Optional[dict]:
        with _LOCK:
            data = self._read()
            event = data["events"].get(event_id)
            if event is None:
                return None
            event.update(changes, updated_at=_now())
            self._write(data)
            return dict(event)


class ProactiveNotificationCoordinator:
    """Queues concise events and hands them to an existing voice session.

    ``voice_send`` only means the existing Live session accepted the prompt;
    ``mark_output_complete`` is required before an event is marked delivered.
    """

    def __init__(
        self,
        *,
        store: Optional[NotificationStore] = None,
        voice_send: Optional[Callable[[str, str], bool]] = None,
        available: Optional[Callable[[], bool]] = None,
        max_pending: int = 25,
    ):
        self.store = store or NotificationStore()
        self.voice_send = voice_send or (lambda _event_id, _text: False)
        self.available = available or (lambda: False)
        self.max_pending = max(1, int(max_pending))
        self.active_event_id: Optional[str] = None
        self._coord_lock = RLock()
        self._recover_attempted()

    def _recover_attempted(self) -> None:
        """Return claims left mid-flight to the durable deferred queue."""
        with self._coord_lock:
            for event in self.store.list(200):
                if event.get("status") == "attempted":
                    self.store.update(
                        event["event_id"],
                        status="deferred",
                        failure="Recovered after interrupted notification delivery.",
                    )

    def enqueue(
        self,
        *,
        event_id: str,
        kind: str,
        text: str,
        priority: str = "ordinary",
        metadata: Optional[dict] = None,
    ) -> dict:
        existing = self.store.get(event_id)
        if existing:
            return existing
        safe_text = redact_sensitive_text(" ".join(str(text or "").split()))[:420]
        if not safe_text:
            raise ValueError("A proactive notification needs text.")
        pending = [item for item in self.store.list(200) if item.get("status") in _ACTIVE]
        status = "queued" if len(pending) < self.max_pending else "dismissed"
        event = {
            "event_id": str(event_id),
            "kind": str(kind or "event"),
            "text": safe_text,
            "priority": "urgent" if str(priority).lower() == "urgent" else "ordinary",
            "status": status,
            "metadata": dict(metadata or {}),
            "attempts": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if status == "dismissed":
            event["failure"] = "Notification backlog limit reached."
        return self.store.put(event)

    def enqueue_reminder(self, record: dict) -> dict:
        occurrence = str(record.get("occurrence") or record.get("next_due") or "")
        event_id = f"reminder:{record.get('id', 'unknown')}:{occurrence}"
        return self.enqueue(
            event_id=event_id,
            kind="reminder",
            text=f"Reminder: {record.get('delivered_message') or record.get('message', '')}",
            priority="ordinary",
            metadata={"reminder_id": record.get("id"), "occurrence": occurrence},
        )

    def enqueue_event(self, event_id: str, kind: str, text: str, *, priority: str = "ordinary", metadata: Optional[dict] = None) -> dict:
        return self.enqueue(event_id=event_id, kind=kind, text=text, priority=priority, metadata=metadata)

    def _next(self) -> Optional[dict]:
        candidates = [item for item in self.store.list(200) if item.get("status") in {"queued", "deferred"}]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.get("priority") != "urgent", item.get("created_at", "")))
        return candidates[0]

    def tick(self) -> Optional[dict]:
        """Attempt at most one event when the existing voice path is free."""
        with self._coord_lock:
            if self.active_event_id or not self.available():
                return None
            event = self._next()
            if event is None:
                return None
            self.active_event_id = event["event_id"]
            claimed = self.store.update(
                event["event_id"],
                status="attempted",
                attempts=int(event.get("attempts") or 0) + 1,
                attempted_at=_now(),
                failure="",
            )
        try:
            accepted = bool(self.voice_send(event["event_id"], event["text"]))
        except Exception as exc:
            with self._coord_lock:
                self.active_event_id = None
                return self.store.update(event["event_id"], status="failed", failure=type(exc).__name__)
        if not accepted:
            with self._coord_lock:
                self.active_event_id = None
                return self.store.update(event["event_id"], status="deferred", failure="Voice session unavailable.")
        return claimed

    def mark_output_complete(self, *, event_id: Optional[str] = None, produced_audio: bool = False) -> Optional[dict]:
        with self._coord_lock:
            target = event_id or self.active_event_id
            if not target or (event_id and self.active_event_id != event_id):
                return None
            self.active_event_id = None
            if produced_audio:
                return self.store.update(target, status="delivered", delivered_at=_now(), failure="")
            return self.store.update(target, status="failed", failure="No voice output was confirmed.")

    def defer_active(self, reason: str = "Voice lifecycle unavailable.") -> Optional[dict]:
        with self._coord_lock:
            target = self.active_event_id
            if not target:
                return None
            self.active_event_id = None
            return self.store.update(target, status="deferred", failure=reason)

    def dismiss_reminder(self, reminder_id: str) -> int:
        count = 0
        for event in self.store.list(200):
            metadata = event.get("metadata") or {}
            if metadata.get("reminder_id") == reminder_id and event.get("status") in _ACTIVE:
                self.store.update(event["event_id"], status="dismissed", failure="Reminder cancelled or rescheduled.")
                count += 1
        return count
