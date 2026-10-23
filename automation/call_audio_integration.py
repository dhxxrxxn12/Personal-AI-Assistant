"""Local simulated-call to audio-lease integration.

This adapter deliberately creates fake streams only.  It is the production
runtime seam for the simulated call milestone and contains no device,
telephony, or network code.
"""
from __future__ import annotations

from threading import RLock
from typing import Optional

from automation.audio_ownership import (
    AudioOwnershipManager,
    FakeAudioStream,
    FakeAudioStreamPair,
    AudioLease,
)
from automation.call_coordinator import CallCoordinator, CallState


class CallAudioBridge:
    """Own one fake capture/playback lease for each connected simulated call."""

    def __init__(self, calls: CallCoordinator, audio: AudioOwnershipManager):
        self.calls = calls
        self.audio = audio
        self._lock = RLock()
        self._leases: dict[str, AudioLease] = {}
        self._errors: dict[str, str] = {}
        calls.add_state_listener(self.on_call_state)

    @property
    def active_call_id(self) -> Optional[str]:
        with self._lock:
            return next(iter(self._leases), None)

    @property
    def errors(self) -> dict[str, str]:
        with self._lock:
            return dict(self._errors)

    def on_call_state(self, record: dict) -> None:
        call_id = str(record.get("call_id") or "")
        state = str(record.get("state") or "")
        if not call_id:
            return
        if state == CallState.CONNECTED.value:
            self._acquire(call_id)
        elif state in {
            CallState.ENDED.value,
            CallState.REJECTED.value,
            CallState.FAILED.value,
            CallState.CANCELLED.value,
        }:
            self._release(call_id)

    def _acquire(self, call_id: str) -> None:
        with self._lock:
            if call_id in self._leases:
                return
        try:
            lease = self.audio.acquire(
                f"call:{call_id}",
                lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream()),
                require_owner_stop=True,
            )
        except Exception as exc:
            self._errors[call_id] = f"Audio acquisition failed: {type(exc).__name__}"
            try:
                current = self.calls.get(call_id)
                if current["state"] == CallState.CONNECTED.value:
                    self.calls.fail(call_id, self._errors[call_id])
            except Exception:
                pass
            return
        with self._lock:
            if call_id in self._leases:
                # A concurrent observer won the race; stop and release this
                # redundant lease rather than leaking it.
                self.audio.stop(lease)
                self.audio.release(lease)
            else:
                self._leases[call_id] = lease

    def _release(self, call_id: str) -> None:
        with self._lock:
            lease = self._leases.pop(call_id, None)
        if lease is None:
            return
        try:
            self.audio.stop(lease)
            self.audio.release(lease)
        except Exception as exc:
            self._errors[call_id] = f"Audio release failed: {type(exc).__name__}"

    def shutdown(self) -> None:
        with self._lock:
            leases = list(self._leases.items())
            self._leases.clear()
        for call_id, lease in leases:
            try:
                self.audio.stop(lease)
                self.audio.release(lease)
            except Exception as exc:
                self._errors[call_id] = f"Audio shutdown failed: {type(exc).__name__}"
        self.audio.shutdown()

    def status(self) -> dict:
        return {
            "owner": self.audio.owner,
            "state": self.audio.state.value,
            "healthy": self.audio.healthy,
            "restoration_failed": not self.audio.healthy and self.audio.state.value == "IDLE",
            "errors": self.errors,
        }
