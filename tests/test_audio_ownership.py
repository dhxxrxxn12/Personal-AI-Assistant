from __future__ import annotations

from queue import Empty
from threading import Event, Thread

import pytest

from automation.audio_ownership import (
    AudioFormat,
    AudioFrame,
    AudioOwnershipManager,
    BackpressureError,
    FakeAudioStream,
    FakeAudioStreamPair,
    InvalidLeaseError,
    OwnershipBusyError,
    OwnershipCancelledError,
    OwnershipFailure,
    OwnershipState,
)


def pair(**kwargs):
    return FakeAudioStreamPair(FakeAudioStream(**kwargs), FakeAudioStream(**kwargs))


def test_acquire_and_idempotent_release():
    manager = AudioOwnershipManager()
    streams = pair()
    lease = manager.acquire("call-1", lambda _cancel: streams)
    assert manager.state is OwnershipState.CALL_AUDIO
    assert manager.owner == "call-1"
    assert manager.release(lease) is True
    assert manager.state is OwnershipState.IDLE
    assert streams.capture.closed and streams.playback.closed
    assert manager.release(lease) is False


def test_conflicting_and_concurrent_acquisition():
    manager = AudioOwnershipManager()
    started = Event()
    continue_factory = Event()
    first_result = []

    def factory(cancel):
        started.set()
        continue_factory.wait()
        return pair()

    thread = Thread(target=lambda: first_result.append(manager.acquire("first", factory)))
    thread.start()
    assert started.wait(timeout=1)
    with pytest.raises(OwnershipBusyError):
        manager.acquire("second", lambda _cancel: pair())
    continue_factory.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    manager.release(first_result[0])


def test_cancellation_during_acquisition_cleans_up():
    manager = AudioOwnershipManager()
    cancelled = Event()

    def factory(cancel):
        cancelled.set()
        raise OwnershipCancelledError("cancelled by test")

    with pytest.raises(OwnershipCancelledError):
        manager.acquire("call", factory, cancel_event=Event())
    assert manager.state is OwnershipState.IDLE
    assert manager.owner is None


def test_acquisition_failure_does_not_lock_manager():
    manager = AudioOwnershipManager()

    def broken(_cancel):
        raise RuntimeError("fake open failed")

    with pytest.raises(OwnershipFailure, match="fake open failed"):
        manager.acquire("call", broken)
    assert manager.state is OwnershipState.IDLE
    lease = manager.acquire("retry", lambda _cancel: pair())
    manager.release(lease)


def test_stale_release_is_rejected_but_repeated_valid_release_is_safe():
    manager = AudioOwnershipManager()
    first = manager.acquire("first", lambda _cancel: pair())
    stale = type(first)(owner="other", token=first.token, streams=first.streams)
    with pytest.raises(InvalidLeaseError):
        manager.release(stale)
    manager.release(first)
    unknown = type(first)(owner="other", token="not-a-real-token", streams=first.streams)
    with pytest.raises(InvalidLeaseError):
        manager.release(unknown)
    second = manager.acquire("second", lambda _cancel: pair())
    with pytest.raises(InvalidLeaseError):
        manager.release(first)
    manager.release(second)


def test_fake_streams_are_bounded_and_codec_metadata_is_preserved():
    stream = FakeAudioStream(max_frames=1)
    frame = AudioFrame(b"one", AudioFormat(codec="mulaw", sample_rate=8000), timestamp=7)
    stream.write(frame)
    with pytest.raises(BackpressureError):
        stream.write(AudioFrame(b"two"))
    assert stream.read() == frame
    with pytest.raises(Empty):
        stream.read()


def test_shutdown_is_safe_idle_and_owned():
    manager = AudioOwnershipManager()
    manager.shutdown()
    lease = manager.acquire("call", lambda _cancel: pair())
    manager.shutdown()
    assert manager.state is OwnershipState.IDLE
    assert lease.streams.capture.closed and lease.streams.playback.closed
    manager.shutdown()


def test_shutdown_cancels_an_inflight_acquisition():
    manager = AudioOwnershipManager()
    started = Event()
    finished = Event()
    result = []

    def factory(cancel):
        started.set()
        while not cancel.is_set():
            finished.wait(timeout=0.01)
        raise OwnershipCancelledError("shutdown")

    thread = Thread(target=lambda: _capture(result, manager, factory))
    thread.start()
    assert started.wait(timeout=1)
    manager.shutdown()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert manager.state is OwnershipState.IDLE
    assert isinstance(result[0], OwnershipCancelledError)


def _capture(result, manager, factory):
    try:
        manager.acquire("call", factory)
    except Exception as exc:
        result.append(exc)


def test_cleanup_after_stream_failure():
    manager = AudioOwnershipManager()
    streams = pair()
    lease = manager.acquire("call", lambda _cancel: streams)
    streams.playback.fail_on_write = True
    with pytest.raises(OwnershipFailure):
        streams.playback.write(AudioFrame(b"bad"))
    manager.fail(lease, "playback failed")
    assert manager.state is OwnershipState.IDLE
    assert streams.capture.closed and streams.playback.closed


class GeminiMock:
    def __init__(self):
        self.suspended = 0
        self.restored = 0
        self.valid = True

    def suspend_for_call(self):
        self.suspended += 1
        return True

    def can_restore_after_call(self):
        return self.valid

    def restore_after_call(self):
        self.restored += 1
        return True


def test_gemini_is_suppressed_and_restored_only_when_valid():
    gemini = GeminiMock()
    manager = AudioOwnershipManager(gemini=gemini)
    lease = manager.acquire("call", lambda _cancel: pair())
    assert gemini.suspended == 1
    manager.release(lease)
    assert gemini.restored == 1
    lease = manager.acquire("call-2", lambda _cancel: pair())
    gemini.valid = False
    manager.release(lease)
    assert gemini.restored == 1


def test_gemini_restored_after_failed_acquisition():
    gemini = GeminiMock()
    manager = AudioOwnershipManager(gemini=gemini)
    with pytest.raises(OwnershipFailure):
        manager.acquire("call", lambda _cancel: (_ for _ in ()).throw(RuntimeError("open")))
    assert manager.state is OwnershipState.IDLE
    assert gemini.suspended == 1
    assert gemini.restored == 1
