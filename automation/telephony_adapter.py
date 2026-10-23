"""Provider-independent telephony adapter boundary and local simulator.

This module contains no provider SDK, socket, device, or network code.  The
adapter translates validated provider-shaped events into the existing
``CallCoordinator`` and exposes audio frames to a caller-owned callback.  It
does not persist calls, approve calls, or own Gemini/audio resources.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Callable, Optional, Protocol

from automation.audio_ownership import AudioFormat
from automation.call_coordinator import CallCoordinator, CallRecord


class ProviderEventType(str, Enum):
    RINGING = "ringing"
    CONNECTED = "connected"
    ENDED = "ended"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class ProviderCapabilities:
    incoming_calls: bool = True
    outgoing_calls: bool = True
    bidirectional_audio: bool = True
    provider_webhooks: bool = False


@dataclass(frozen=True)
class ProviderEvent:
    call_id: str
    event_id: str
    event_type: ProviderEventType


@dataclass(frozen=True)
class ProviderAudioFrame:
    call_id: str
    frame_id: str
    payload: bytes
    timestamp: int = 0
    audio_format: AudioFormat = AudioFormat()


class TelephonyAdapter(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def ready(self) -> bool: ...
    def start_outgoing(self, call: CallRecord) -> None: ...
    def answer_incoming(self, call: CallRecord) -> None: ...
    def hangup(self, call: CallRecord) -> None: ...


class ProviderEventError(ValueError):
    """Raised before malformed provider data reaches state mutation."""


class SimulatedTelephonyAdapter:
    """Deterministic local adapter for future provider integration tests."""

    _EVENTS = {item.value: item for item in ProviderEventType}

    def __init__(
        self,
        *,
        coordinator: Optional[CallCoordinator] = None,
        audio_frame_handler: Optional[Callable[[ProviderAudioFrame], None]] = None,
    ) -> None:
        self.coordinator = coordinator
        self.audio_frame_handler = audio_frame_handler
        self.operations: list[tuple[str, str]] = []
        self.received_events: list[str] = []
        self._received_frames: set[tuple[str, str]] = set()
        self._lock = RLock()
        self._ready = True

    def bind(self, coordinator: CallCoordinator) -> None:
        if self.coordinator is not None and self.coordinator is not coordinator:
            raise RuntimeError("Adapter is already bound to a different coordinator.")
        self.coordinator = coordinator

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def set_ready(self, ready: bool) -> None:
        with self._lock:
            self._ready = bool(ready)

    def _require_ready(self) -> None:
        if not self.ready():
            raise RuntimeError("Simulated telephony adapter is unavailable.")

    def _require_coordinator(self) -> CallCoordinator:
        if self.coordinator is None:
            raise RuntimeError("The telephony adapter is not bound to a CallCoordinator.")
        return self.coordinator

    def start_outgoing(self, call: CallRecord) -> None:
        self._require_ready()
        with self._lock:
            self.operations.append(("start_outgoing", call.call_id))

    def answer_incoming(self, call: CallRecord) -> None:
        self._require_ready()
        with self._lock:
            self.operations.append(("answer_incoming", call.call_id))

    def hangup(self, call: CallRecord) -> None:
        with self._lock:
            self.operations.append(("hangup", call.call_id))

    @classmethod
    def parse_event(cls, payload: object) -> ProviderEvent:
        if not isinstance(payload, dict):
            raise ProviderEventError("Provider event must be an object.")
        call_id = payload.get("call_id")
        event_id = payload.get("event_id")
        event_type = payload.get("event")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ProviderEventError("Provider event needs a call_id.")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ProviderEventError("Provider event needs an event_id.")
        if not isinstance(event_type, str) or event_type.lower() not in cls._EVENTS:
            raise ProviderEventError("Provider event has an unsupported event type.")
        return ProviderEvent(call_id.strip(), event_id.strip(), cls._EVENTS[event_type.lower()])

    def receive_incoming(self, destination: str, purpose: str = "Incoming simulated call", *, event_id: str) -> dict:
        self._require_ready()
        if not isinstance(event_id, str) or not event_id.strip():
            raise ProviderEventError("Incoming call needs an event_id.")
        return self._require_coordinator().simulate_incoming(
            destination, purpose, provider_event_id=event_id.strip()
        )

    def emit_event(self, payload: object) -> dict:
        event = self.parse_event(payload)
        self._require_ready()
        with self._lock:
            if event.event_id not in self.received_events:
                self.received_events.append(event.event_id)
        return self._require_coordinator().event(
            event.call_id, event.event_type.value, provider_event_id=event.event_id
        )

    def emit_audio(
        self,
        call_id: str,
        payload: bytes,
        *,
        frame_id: str,
        timestamp: int = 0,
        audio_format: AudioFormat = AudioFormat(),
    ) -> ProviderAudioFrame:
        self._require_ready()
        if not isinstance(call_id, str) or not call_id.strip():
            raise ProviderEventError("Audio frame needs a call_id.")
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise ProviderEventError("Audio frame needs a frame_id.")
        if not isinstance(payload, bytes):
            raise ProviderEventError("Audio frame payload must be bytes.")
        coordinator = self._require_coordinator()
        if coordinator.get(call_id.strip())["state"] != "connected":
            raise ProviderEventError("Audio frames are accepted only for connected calls.")
        frame = ProviderAudioFrame(call_id.strip(), frame_id.strip(), payload, int(timestamp), audio_format)
        with self._lock:
            duplicate = (frame.call_id, frame.frame_id) in self._received_frames
            self._received_frames.add((frame.call_id, frame.frame_id))
        if duplicate:
            return frame
        if self.audio_frame_handler is not None:
            self.audio_frame_handler(frame)
        return frame

    def disconnect(
        self,
        call_id: Optional[str] = None,
        *,
        event_id: str = "provider-disconnected",
        reason: str = "Provider disconnected",
    ) -> Optional[dict]:
        with self._lock:
            self._ready = False
        if call_id is None:
            # The coordinator remains the authority for active-call recovery;
            # without a call ID this is only an adapter readiness change.
            return None
        result = self._require_coordinator().event(
            call_id, ProviderEventType.FAILED.value, provider_event_id=event_id
        )
        _ = reason  # CallCoordinator owns redacted failure text and persistence.
        return result
