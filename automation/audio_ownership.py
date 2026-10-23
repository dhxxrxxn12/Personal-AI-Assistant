"""Local, deterministic audio ownership primitives for simulated calls.

This module intentionally does not import sounddevice, Gemini, telephony SDKs,
or networking code.  It provides the smallest lease and fake-stream boundary
needed before any real call adapter can be considered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import os
from queue import Empty, Full, Queue
from threading import Event, RLock
from typing import Callable, Optional, Protocol


AUDIO_GATE_ENV = "JARVIS_ENABLE_GEMINI_AUDIO_GATE"


def audio_gate_enabled(value: Optional[str] = None) -> bool:
    """Return the opt-in flag value; the default is deliberately disabled."""
    raw = value if value is not None else os.environ.get(AUDIO_GATE_ENV, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class OwnershipState(str, Enum):
    IDLE = "IDLE"
    ACQUIRING = "ACQUIRING"
    CALL_AUDIO = "CALL_AUDIO"
    RELEASING = "RELEASING"


class AudioOwnershipError(RuntimeError):
    """Base error for ownership and fake-stream failures."""


class OwnershipBusyError(AudioOwnershipError):
    pass


class OwnershipCancelledError(AudioOwnershipError):
    pass


class OwnershipFailure(AudioOwnershipError):
    pass


class InvalidLeaseError(AudioOwnershipError):
    pass


class BackpressureError(AudioOwnershipError):
    pass


@dataclass(frozen=True)
class AudioFormat:
    codec: str = "pcm_s16le"
    sample_rate: int = 16_000
    channels: int = 1


@dataclass(frozen=True)
class AudioFrame:
    payload: bytes
    audio_format: AudioFormat = AudioFormat()
    timestamp: int = 0
    end_of_stream: bool = False


class AudioStream(Protocol):
    def write(self, frame: AudioFrame) -> None: ...
    def read(self) -> AudioFrame: ...
    def close(self) -> None: ...


class AudioStreamPair(Protocol):
    capture: AudioStream
    playback: AudioStream


class GeminiAudioController(Protocol):
    """Minimal lifecycle hook; an implementation must validate restoration."""

    def suspend_for_call(self) -> bool: ...
    def can_restore_after_call(self) -> bool: ...
    def restore_after_call(self) -> bool: ...


class _CancellationSignal:
    """Event-like view combining caller cancellation with manager shutdown."""

    def __init__(self, caller: Optional[Event], shutdown: Event):
        self._caller = caller
        self._shutdown = shutdown

    def is_set(self) -> bool:
        return bool((self._caller and self._caller.is_set()) or self._shutdown.is_set())


class FakeAudioStream:
    """A bounded, deterministic stream used by tests and simulations only."""

    def __init__(self, *, max_frames: int = 8, fail_on_write: bool = False, fail_on_read: bool = False):
        if int(max_frames) < 1:
            raise ValueError("max_frames must be positive")
        self._queue: Queue[AudioFrame] = Queue(maxsize=int(max_frames))
        self.fail_on_write = fail_on_write
        self.fail_on_read = fail_on_read
        self.closed = False
        self.write_count = 0
        self.read_count = 0

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def write(self, frame: AudioFrame) -> None:
        if self.closed:
            raise OwnershipFailure("Audio stream is closed.")
        if self.fail_on_write:
            raise OwnershipFailure("Fake audio write failed.")
        try:
            self._queue.put_nowait(frame)
        except Full as exc:
            raise BackpressureError("Audio stream queue is full.") from exc
        self.write_count += 1

    def read(self) -> AudioFrame:
        if self.closed and self._queue.empty():
            raise OwnershipFailure("Audio stream is closed.")
        if self.fail_on_read:
            raise OwnershipFailure("Fake audio read failed.")
        try:
            frame = self._queue.get_nowait()
        except Empty as exc:
            raise Empty("Audio stream has no frame available.") from exc
        self.read_count += 1
        return frame

    def close(self) -> None:
        self.closed = True
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                return


@dataclass
class FakeAudioStreamPair:
    capture: FakeAudioStream
    playback: FakeAudioStream


@dataclass(frozen=True)
class AudioLease:
    owner: str
    token: str
    streams: AudioStreamPair
    require_owner_stop: bool = False
    stop_event: Event = field(default_factory=Event, compare=False, repr=False)

    def mark_stopped(self) -> None:
        """Mark the owner-side audio workers stopped before release."""
        self.stop_event.set()


class AudioOwnershipManager:
    """Coordinates one exclusive capture/playback lease.

    Acquisition is deliberately non-blocking: a second caller receives
    ``OwnershipBusyError`` instead of waiting unpredictably.  Stream creation
    happens while the manager is ``ACQUIRING`` so concurrent callers cannot
    overtake a failed or cancellable acquisition.
    """

    def __init__(self, *, gemini: Optional[GeminiAudioController] = None):
        self._lock = RLock()
        self._state = OwnershipState.IDLE
        self._lease: Optional[AudioLease] = None
        self._generation = 0
        self._gemini = gemini
        self.last_error = ""
        self._shutdown_event: Optional[Event] = None
        self._released_tokens: set[str] = set()
        self._releasing_token: Optional[str] = None
        self._restoration_failed = False

    @property
    def state(self) -> OwnershipState:
        with self._lock:
            return self._state

    @property
    def owner(self) -> Optional[str]:
        with self._lock:
            return self._lease.owner if self._lease else None

    @property
    def healthy(self) -> bool:
        with self._lock:
            return self._state is OwnershipState.IDLE and not self._restoration_failed

    def acquire(
        self,
        owner: str,
        stream_factory: Callable[[Event], AudioStreamPair],
        *,
        cancel_event: Optional[Event] = None,
        require_owner_stop: bool = False,
    ) -> AudioLease:
        owner = str(owner or "").strip()
        if not owner:
            raise ValueError("An audio owner identity is required.")
        shutdown_event = Event()
        with self._lock:
            if self._state != OwnershipState.IDLE or self._lease is not None:
                raise OwnershipBusyError(f"Audio is unavailable in state {self._state.value}.")
            if self._restoration_failed:
                raise OwnershipFailure("Gemini audio restoration has not been confirmed.")
            self._state = OwnershipState.ACQUIRING
            self.last_error = ""
            self._restoration_failed = False
            self._shutdown_event = shutdown_event

        streams: Optional[AudioStreamPair] = None
        suspended = False
        cancellation = _CancellationSignal(cancel_event, shutdown_event)
        try:
            if cancellation.is_set():
                raise OwnershipCancelledError("Audio acquisition was cancelled.")
            if self._gemini is not None:
                suspended = bool(self._gemini.suspend_for_call())
                if not suspended:
                    raise OwnershipFailure("Gemini audio could not be suspended safely.")
            if cancellation.is_set():
                raise OwnershipCancelledError("Audio acquisition was cancelled.")
            streams = stream_factory(cancellation)
            if streams is None or streams.capture is None or streams.playback is None:
                raise OwnershipFailure("A capture and playback stream are both required.")
            if cancellation.is_set():
                raise OwnershipCancelledError("Audio acquisition was cancelled.")
            with self._lock:
                self._generation += 1
                lease = AudioLease(
                    owner=owner,
                    token=f"audio-{self._generation}",
                    streams=streams,
                    require_owner_stop=bool(require_owner_stop),
                )
                self._lease = lease
                self._state = OwnershipState.CALL_AUDIO
                self._shutdown_event = None
            return lease
        except Exception as exc:
            if streams is not None:
                self._close_streams(streams)
            if suspended and self._gemini is not None:
                if not self._restore_gemini():
                    with self._lock:
                        self._restoration_failed = True
            with self._lock:
                self.last_error = str(exc)
                self._lease = None
                self._state = OwnershipState.IDLE
                self._shutdown_event = None
            if isinstance(exc, AudioOwnershipError):
                raise
            raise OwnershipFailure(str(exc)) from exc

    def release(self, lease: AudioLease) -> bool:
        with self._lock:
            current = self._lease
            if current is None:
                if isinstance(lease, AudioLease) and lease.token in self._released_tokens:
                    return False
                raise InvalidLeaseError("The audio lease is not recognized.")
            if self._state is OwnershipState.RELEASING:
                if lease.token == self._releasing_token:
                    return False
                raise InvalidLeaseError("The audio lease owner or token is stale.")
            if lease.token != current.token or lease.owner != current.owner:
                raise InvalidLeaseError("The audio lease owner or token is stale.")
            if current.require_owner_stop and not current.stop_event.is_set():
                raise AudioOwnershipError("Audio owner must stop its workers before release.")
            self._state = OwnershipState.RELEASING
            self._releasing_token = current.token

        errors = self._close_streams(current.streams)
        restored = self._restore_gemini() if self._gemini is not None else True
        with self._lock:
            if errors:
                self.last_error = "; ".join(errors)
            if not restored:
                self._restoration_failed = True
                self.last_error = "; ".join(item for item in (self.last_error, "Gemini audio restoration was not confirmed.") if item)
            self._lease = None
            self._released_tokens.add(current.token)
            self._releasing_token = None
            self._state = OwnershipState.IDLE
        return True

    def stop(self, lease: AudioLease) -> bool:
        """Validate a lease and record that its owner stopped using audio."""
        with self._lock:
            current = self._lease
            if current is None or current.token != lease.token or current.owner != lease.owner:
                raise InvalidLeaseError("The audio lease owner or token is stale.")
            current.mark_stopped()
            return True

    def fail(self, lease: AudioLease, reason: str = "Audio stream failed.") -> None:
        """Release after a stream/provider failure without leaking ownership."""
        with self._lock:
            self.last_error = str(reason)[:240]
            if lease.require_owner_stop:
                lease.mark_stopped()
        self.release(lease)

    def shutdown(self) -> None:
        with self._lock:
            lease = self._lease
            acquisition = self._shutdown_event
            if acquisition is not None:
                acquisition.set()
        if lease is not None:
            # Shutdown is the owner-side stop signal when the runtime is being
            # torn down; normal call completion must use ``stop`` explicitly.
            if lease.require_owner_stop:
                lease.mark_stopped()
            self.release(lease)
        else:
            with self._lock:
                if acquisition is None:
                    self._state = OwnershipState.IDLE

    def _close_streams(self, streams: AudioStreamPair) -> list[str]:
        errors = []
        for stream in (streams.capture, streams.playback):
            try:
                stream.close()
            except Exception as exc:
                errors.append(str(exc)[:160])
        return errors

    def _restore_gemini(self) -> bool:
        controller = self._gemini
        if controller is None:
            return True
        try:
            if controller.can_restore_after_call():
                restored = bool(controller.restore_after_call())
                if not restored:
                    return False
                return True
            return False
        except Exception as exc:
            with self._lock:
                self.last_error = "; ".join(item for item in (self.last_error, str(exc)[:160]) if item)
            return False
