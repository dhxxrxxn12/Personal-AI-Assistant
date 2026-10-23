"""Provider-independent simulated call coordination.

This module deliberately has no audio, network, telephony, or Gemini
dependencies.  A future provider may implement :class:`CallProvider`, while
the coordinator remains the authority for state, approval, persistence, and
notifications.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Optional, Protocol

from automation.proactive_notifications import ProactiveNotificationCoordinator
from core.privacy import redact_sensitive_text, redact_sensitive_value
from core.runtime_config import runtime_path

DEFAULT_PATH = runtime_path("calls.json", Path.home() / ".jarvis" / "calls.json")
_LOCK = RLock()


class CallState(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RINGING = "ringing"
    CONNECTED = "connected"
    ENDED = "ended"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IncomingCallPolicy(str, Enum):
    NOTIFY_ONLY = "notify_only"
    ANSWER_SIMULATED = "answer_simulated"


TERMINAL = {CallState.ENDED.value, CallState.REJECTED.value, CallState.FAILED.value, CallState.CANCELLED.value}
TRANSITIONS = {
    CallState.PENDING_APPROVAL.value: {CallState.APPROVED.value, CallState.REJECTED.value, CallState.CANCELLED.value},
    CallState.APPROVED.value: {CallState.RINGING.value, CallState.CANCELLED.value, CallState.FAILED.value},
    CallState.RINGING.value: {CallState.CONNECTED.value, CallState.ENDED.value, CallState.REJECTED.value, CallState.CANCELLED.value, CallState.FAILED.value},
    CallState.CONNECTED.value: {CallState.ENDED.value, CallState.FAILED.value, CallState.CANCELLED.value},
}


@dataclass
class CallRecord:
    call_id: str
    direction: str
    destination: str
    purpose: str
    state: str
    created_at: str
    updated_at: str
    provider_event_id: Optional[str] = None
    failure_reason: str = ""
    processed_event_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class CallProvider(Protocol):
    """Future provider boundary; implementations must not own call policy."""

    def start_outgoing(self, call: CallRecord) -> None: ...
    def answer_incoming(self, call: CallRecord) -> None: ...
    def hangup(self, call: CallRecord) -> None: ...


class SimulatedCallProvider:
    """A deterministic provider that only records local simulated operations."""

    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []

    def start_outgoing(self, call: CallRecord) -> None:
        self.operations.append(("start_outgoing", call.call_id))

    def answer_incoming(self, call: CallRecord) -> None:
        self.operations.append(("answer_incoming", call.call_id))

    def hangup(self, call: CallRecord) -> None:
        self.operations.append(("hangup", call.call_id))


class CallStore:
    """Atomic local JSON storage for call metadata and state."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "calls": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("calls"), dict):
                return data
        except (OSError, ValueError):
            pass
        raise OSError("Call storage is unreadable or corrupt.")

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".calls-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def get(self, call_id: str) -> Optional[dict]:
        with _LOCK:
            value = self._read()["calls"].get(call_id)
            return dict(value) if value else None

    def list(self, limit: int = 100) -> list[dict]:
        with _LOCK:
            values = list(self._read()["calls"].values())
        return sorted(values, key=lambda item: item.get("updated_at", ""), reverse=True)[:max(1, int(limit))]

    def put(self, record: dict) -> dict:
        with _LOCK:
            data = self._read()
            data["calls"][record["call_id"]] = record
            self._write(data)
        return record


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CallCoordinator:
    def __init__(self, *, store: Optional[CallStore] = None, provider: Optional[CallProvider] = None,
                 notifications: Optional[ProactiveNotificationCoordinator] = None,
                 incoming_policy: IncomingCallPolicy | str = IncomingCallPolicy.NOTIFY_ONLY,
                 recover: bool = True,
                 state_listener: Optional[Callable[[dict], None]] = None):
        self.store = store or CallStore()
        self.provider = provider or SimulatedCallProvider()
        self.notifications = notifications or ProactiveNotificationCoordinator()
        self.incoming_policy = incoming_policy if isinstance(incoming_policy, IncomingCallPolicy) else IncomingCallPolicy(str(incoming_policy))
        self._listeners: list[Callable[[dict], None]] = []
        if state_listener is not None:
            self._listeners.append(state_listener)
        if recover:
            self.recover_interrupted()

    def add_state_listener(self, listener: Callable[[dict], None]) -> None:
        if not callable(listener):
            raise TypeError("Call state listener must be callable.")
        self._listeners.append(listener)

    def _publish(self, record: dict) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(dict(record))
            except Exception:
                # Persistence and call state remain authoritative; an observer
                # must never turn a completed state transition into an error.
                continue

    def _record(self, call: CallRecord) -> dict:
        call.updated_at = _now()
        return self.store.put(asdict(call))

    def _get(self, call_id: str) -> CallRecord:
        raw = self.store.get(call_id)
        if not raw:
            raise KeyError("Call not found.")
        return CallRecord(**raw)

    def _notify(self, call: CallRecord, detail: str) -> None:
        destination = redact_sensitive_text(call.destination)[:120]
        purpose = redact_sensitive_text(call.purpose)[:180]
        text = f"Simulated {call.direction} call {call.state}: {destination}. Purpose: {purpose}. {detail}"
        self.notifications.enqueue(event_id=f"call:{call.call_id}:{call.state}", kind="call_status", text=text,
                                   metadata={"call_id": call.call_id, "state": call.state})

    def _transition(self, call: CallRecord, state: CallState | str, *, detail: str = "", reason: str = "") -> dict:
        target = state.value if isinstance(state, CallState) else str(state)
        if target not in TRANSITIONS.get(call.state, set()):
            raise ValueError(f"Invalid call transition: {call.state} -> {target}.")
        call.state = target
        if reason:
            call.failure_reason = redact_sensitive_text(reason)[:240]
        record = self._record(call)
        self._notify(call, detail or "Status updated.")
        self._publish(record)
        return record

    def request_outgoing(self, destination: str, purpose: str, metadata: Optional[dict] = None) -> dict:
        destination = " ".join(str(destination or "").split())
        purpose = " ".join(str(purpose or "").split())
        if not destination or not purpose:
            raise ValueError("An outgoing call needs both a destination and purpose.")
        now = _now()
        call = CallRecord(uuid.uuid4().hex, "outgoing", redact_sensitive_text(destination)[:180],
                          redact_sensitive_text(purpose)[:300], CallState.PENDING_APPROVAL.value, now, now,
                          metadata=redact_sensitive_value(metadata or {}) if isinstance(metadata or {}, dict) else {})
        record = self._record(call)
        self._notify(call, "Explicit approval is required before simulation can start.")
        self._publish(record)
        return record

    def approve_outgoing(self, call_id: str) -> dict:
        call = self._get(call_id)
        if call.direction != "outgoing" or call.state != CallState.PENDING_APPROVAL.value:
            raise ValueError("Only a pending outgoing call can be approved.")
        record = self._transition(call, CallState.APPROVED, detail="Approved for simulated dialing.")
        try:
            self.provider.start_outgoing(CallRecord(**record))
        except Exception as exc:
            return self.fail(call_id, f"Simulated provider failed: {exc}")
        return record

    def reject_outgoing(self, call_id: str) -> dict:
        call = self._get(call_id)
        return self._transition(call, CallState.REJECTED, detail="Outgoing call was rejected.")

    def simulate_incoming(self, destination: str, purpose: str = "Incoming simulated call", *, provider_event_id: Optional[str] = None) -> dict:
        if provider_event_id:
            with _LOCK:
                for existing in self.store.list(1000):
                    if (existing.get("direction") == "incoming"
                            and existing.get("provider_event_id") == provider_event_id):
                        return existing
        now = _now()
        call = CallRecord(uuid.uuid4().hex, "incoming", redact_sensitive_text(destination)[:180],
                          redact_sensitive_text(purpose)[:300], CallState.RINGING.value, now, now,
                          provider_event_id=provider_event_id)
        record = self._record(call)
        self._notify(call, "Notify-only policy leaves the call unanswered." if self.incoming_policy == IncomingCallPolicy.NOTIFY_ONLY else "Simulated answering is enabled.")
        self._publish(record)
        return record

    def answer_incoming(self, call_id: str) -> dict:
        call = self._get(call_id)
        if self.incoming_policy != IncomingCallPolicy.ANSWER_SIMULATED:
            raise PermissionError("Incoming policy is notify_only; simulated answering is disabled.")
        if call.direction != "incoming" or call.state != CallState.RINGING.value:
            raise ValueError("Only a ringing incoming call can be answered.")
        record = self._transition(call, CallState.CONNECTED, detail="Simulated call connected.")
        try:
            self.provider.answer_incoming(CallRecord(**record))
        except Exception as exc:
            return self.fail(call_id, f"Simulated provider failed: {exc}")
        return record

    def event(self, call_id: str, event: str, *, provider_event_id: Optional[str] = None) -> dict:
        call = self._get(call_id)
        if provider_event_id and provider_event_id in call.processed_event_ids:
            return asdict(call)
        if call.state in TERMINAL:
            if provider_event_id:
                call.processed_event_ids.append(provider_event_id)
                self._record(call)
            return asdict(call)
        mapping = {"ringing": CallState.RINGING, "connected": CallState.CONNECTED, "ended": CallState.ENDED,
                   "rejected": CallState.REJECTED, "cancelled": CallState.CANCELLED, "failed": CallState.FAILED}
        target = mapping.get(str(event).lower())
        if target is None:
            raise ValueError("Unknown simulated call event.")
        if target.value not in TRANSITIONS.get(call.state, set()):
            raise ValueError(f"Invalid call transition: {call.state} -> {target.value}.")
        if provider_event_id:
            call.processed_event_ids.append(provider_event_id)
        return self._transition(call, target, detail=f"Simulated event: {target.value}.")

    def cancel(self, call_id: str) -> dict:
        call = self._get(call_id)
        if call.state == CallState.RINGING.value or call.state == CallState.CONNECTED.value:
            try: self.provider.hangup(call)
            except Exception as exc: return self.fail(call_id, f"Simulated provider failed: {exc}")
        return self._transition(call, CallState.CANCELLED, detail="Call cancelled.")

    def end(self, call_id: str) -> dict:
        return self._transition(self._get(call_id), CallState.ENDED, detail="Call ended.")

    def fail(self, call_id: str, reason: str) -> dict:
        return self._transition(self._get(call_id), CallState.FAILED, detail="Call failed.", reason=reason)

    def recover_interrupted(self) -> list[dict]:
        recovered = []
        for raw in self.store.list(1000):
            if raw.get("state") in {CallState.APPROVED.value, CallState.RINGING.value, CallState.CONNECTED.value}:
                recovered.append(self.fail(raw["call_id"], "Simulated call interrupted by coordinator restart."))
        return recovered

    def get(self, call_id: str) -> dict:
        return asdict(self._get(call_id))

    def history(self, limit: int = 100) -> list[dict]:
        return self.store.list(limit)


_DEFAULT_COORDINATOR: Optional[CallCoordinator] = None


def get_default_call_coordinator() -> CallCoordinator:
    global _DEFAULT_COORDINATOR
    if _DEFAULT_COORDINATOR is None:
        _DEFAULT_COORDINATOR = CallCoordinator()
    return _DEFAULT_COORDINATOR
