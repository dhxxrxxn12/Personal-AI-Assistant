"""cloud/activity_log.py — live event bus for the JARVIS command center.

The HUD needs a truthful activity feed.  Instead of inventing events in the
browser, the Cloud server publishes one record here for every state transition
it already performs: planning, queuing, leasing, execution results, permission
gates, device presence, uploads and errors.

Design notes
------------
* Bounded ring buffer — a long-running server can never grow this in memory.
* Monotonic ``seq`` — clients resume after a reconnect with ``?since=<seq>``.
* Optional asyncio fan-out — ``/activity/stream`` (SSE) subscribes to new
  records; publishing from a worker thread is safe via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

# Categories mirror the HUD legend: SYS PLAN TASK EXEC AGENT DEV USER PERM ERROR
CATEGORIES = ("SYS", "PLAN", "TASK", "EXEC", "AGENT", "DEV", "USER", "PERM", "ERROR")

MAX_EVENTS = 500
DEFAULT_LIMIT = 200


class ActivityLog:
    """Thread-safe, bounded, sequence-numbered event log."""

    def __init__(self, capacity: int = MAX_EVENTS, path: Optional[Path] = None) -> None:
        self._events: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._seq = 0
        self._subscribers: List[Any] = []
        self._loop: Optional[Any] = None
        self._path = Path(path) if path else None
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            events = data.get("events", []) if isinstance(data, dict) else []
            if isinstance(events, list):
                for event in events[-self._events.maxlen:]:
                    if isinstance(event, dict) and isinstance(event.get("seq"), int):
                        self._events.append(event)
                self._seq = max(
                    int(data.get("seq", 0)) if isinstance(data, dict) else 0,
                    max((int(event["seq"]) for event in self._events), default=0),
                )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A corrupt activity cache must not prevent the Cloud from starting.
            self._events.clear()
            self._seq = 0

    def _persist_locked(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"seq": self._seq, "events": list(self._events)}, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError:
            # Activity is observability; failure to persist it must not break a task.
            pass

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def attach_loop(self, loop: Any) -> None:
        """Remember the running event loop so worker threads can fan out."""
        self._loop = loop

    def publish(
        self,
        category: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        level: str = "info",
    ) -> Dict[str, Any]:
        """Append one event and notify SSE subscribers."""
        category = str(category).upper()
        if category not in CATEGORIES:
            category = "SYS"

        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "category": category,
                "level": level,
                "message": str(message),
                "data": data or {},
                "at": datetime.now(timezone.utc).isoformat(),
            }
            self._events.append(event)
            self._persist_locked()

        self._fanout(event)
        return event

    def _fanout(self, event: Dict[str, Any]) -> None:
        loop = self._loop
        with self._lock:
            subscribers = list(self._subscribers)
        if not subscribers or loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._deliver, event)
        except RuntimeError:
            # Loop closed (server shutting down) — keep the buffered events.
            pass

    def _deliver(self, event: Dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except Exception:
                # A slow client must never break the producer.
                pass

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def since(self, seq: int = 0, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        """Return events with ``seq`` greater than the supplied cursor."""
        try:
            cursor = int(seq)
        except (TypeError, ValueError):
            cursor = 0
        with self._lock:
            events = [event for event in self._events if event["seq"] > cursor]
        if limit and limit > 0:
            return events[-limit:]
        return events

    def latest(self, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        if limit and limit > 0:
            return events[-limit:]
        return events

    def cursor(self) -> int:
        with self._lock:
            return self._seq

    def snapshot(self, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        return {
            "cursor": self.cursor(),
            "events": self.latest(limit),
        }

    # ------------------------------------------------------------------
    # SSE subscriptions
    # ------------------------------------------------------------------

    def subscribe(self) -> Any:
        import asyncio

        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: Any) -> None:
        with self._lock:
            try:
                self._subscribers.remove(queue)
            except ValueError:
                pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


# Module-level singleton shared by the Cloud API. Unit-test instances created
# without a path remain in-memory; the service singleton survives restarts.
try:
    from cloud.settings import settings
    activity = ActivityLog(path=settings.activity_file)
except Exception:  # pragma: no cover - defensive import fallback
    activity = ActivityLog()
