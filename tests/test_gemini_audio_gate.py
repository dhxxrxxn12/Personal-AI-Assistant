from __future__ import annotations

import asyncio
from queue import Queue
from threading import Event
from unittest.mock import Mock

import pytest

from main import JarvisLive
from automation.audio_ownership import (
    AudioOwnershipManager,
    FakeAudioStreamPair,
    FakeAudioStream,
    OwnershipBusyError,
    OwnershipFailure,
    OwnershipState,
    audio_gate_enabled,
)


def fake_jarvis(*, enabled=True):
    jarvis = object.__new__(JarvisLive)
    jarvis._audio_gate_enabled = enabled
    jarvis._audio_gate_session = None
    jarvis._audio_gate_generation = 0
    jarvis._gemini_audio_suspended = False
    jarvis._speaking_lock = __import__("threading").Lock()
    jarvis._is_speaking = False
    jarvis.session = object()
    jarvis._reconnect_event = asyncio.Event()
    jarvis.audio_in_queue = Queue()
    jarvis._proactive_coordinator = Mock()
    jarvis.set_speaking = Mock()
    return jarvis


def test_feature_flag_is_disabled_by_default():
    assert audio_gate_enabled("") is False
    assert audio_gate_enabled("0") is False
    assert audio_gate_enabled("true") is True


def test_feature_flag_off_keeps_legacy_path_and_has_no_gate():
    jarvis = fake_jarvis(enabled=False)
    assert jarvis.suspend_for_call() is False
    assert jarvis._gemini_audio_suspended is False


def test_enabled_gate_suspends_and_restores_current_session():
    jarvis = fake_jarvis()
    jarvis.audio_in_queue.put(b"queued")
    assert jarvis.suspend_for_call() is True
    assert jarvis._gemini_audio_suspended is True
    assert jarvis.audio_in_queue.empty()
    assert jarvis._proactive_coordinator.defer_active.called
    assert jarvis.can_restore_after_call() is True
    assert jarvis.restore_after_call() is True
    assert jarvis._gemini_audio_suspended is False


def test_manager_uses_the_jarvis_lifecycle_controller():
    jarvis = fake_jarvis()
    manager = AudioOwnershipManager(gemini=jarvis)
    lease = manager.acquire(
        "call",
        lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream()),
    )
    assert jarvis._gemini_audio_suspended is True
    manager.release(lease)
    assert jarvis._gemini_audio_suspended is False
    assert manager.healthy is True


def test_gate_rejects_restoration_after_session_replacement():
    jarvis = fake_jarvis()
    assert jarvis.suspend_for_call() is True
    jarvis.session = object()
    assert jarvis.can_restore_after_call() is False
    assert jarvis.restore_after_call() is False
    assert jarvis._gemini_audio_suspended is True


def test_proactive_voice_availability_respects_call_audio():
    jarvis = fake_jarvis()
    jarvis._awake = True
    jarvis.ui = Mock(muted=False)
    jarvis._interrupted = False
    jarvis._last_user_speech = 0
    jarvis._gemini_audio_suspended = True
    assert jarvis._proactive_voice_available() is False


def test_manager_does_not_report_healthy_idle_after_restore_failure():
    class RestoreFails:
        def suspend_for_call(self): return True
        def can_restore_after_call(self): return True
        def restore_after_call(self): return False

    manager = AudioOwnershipManager(gemini=RestoreFails())
    streams = FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream())
    lease = manager.acquire("call", lambda _cancel: streams)
    manager.release(lease)
    assert manager.state is OwnershipState.IDLE
    assert manager.healthy is False
    try:
        manager.acquire("again", lambda _cancel: streams)
    except OwnershipFailure:
        pass
    else:
        raise AssertionError("unrestored Gemini audio must block reacquisition")


def test_manager_failed_restoration_does_not_deadlock_on_shutdown():
    class RestoreRaises:
        def suspend_for_call(self): return True
        def can_restore_after_call(self): return True
        def restore_after_call(self): raise RuntimeError("restore failed")

    manager = AudioOwnershipManager(gemini=RestoreRaises())
    lease = manager.acquire("call", lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream()))
    manager.shutdown()
    assert manager.state is OwnershipState.IDLE
    assert manager.healthy is False


def test_shutdown_during_release_waits_for_cleanup_without_second_owner():
    started = Event()
    continue_close = Event()

    class SlowStream(FakeAudioStream):
        def close(self):
            started.set()
            continue_close.wait(timeout=1)
            super().close()

    capture = SlowStream()
    playback = SlowStream()
    manager = AudioOwnershipManager()
    lease = manager.acquire("call", lambda _cancel: FakeAudioStreamPair(capture, playback))
    release_done = Event()

    def release():
        manager.release(lease)
        release_done.set()

    import threading
    thread = threading.Thread(target=release)
    thread.start()
    assert started.wait(timeout=1)
    manager.shutdown()
    assert manager.state is OwnershipState.RELEASING
    with pytest.raises(OwnershipBusyError):
        manager.acquire("other", lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream()))
    continue_close.set()
    assert release_done.wait(timeout=1)
    thread.join(timeout=1)
    assert manager.state is OwnershipState.IDLE


def test_suspension_exception_recovers_to_idle():
    class SuspendRaises:
        def suspend_for_call(self): raise RuntimeError("suspend failed")
        def can_restore_after_call(self): return True
        def restore_after_call(self): return True

    manager = AudioOwnershipManager(gemini=SuspendRaises())
    with pytest.raises(OwnershipFailure):
        manager.acquire("call", lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream()))
    assert manager.state is OwnershipState.IDLE
