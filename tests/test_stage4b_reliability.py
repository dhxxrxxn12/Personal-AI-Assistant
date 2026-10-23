from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from automation.audio_ownership import (
    AudioOwnershipError,
    AudioOwnershipManager,
    FakeAudioStream,
    FakeAudioStreamPair,
    OwnershipState,
)
from automation.call_audio_integration import CallAudioBridge
from automation.call_coordinator import CallCoordinator, CallState, CallStore, IncomingCallPolicy, SimulatedCallProvider
from automation.proactive_notifications import NotificationStore, ProactiveNotificationCoordinator
from dashboard.local_command_center import LocalCommandCenter


class GeminiStub:
    def __init__(self):
        self.session = object()
        self._audio_gate_enabled = True
        self._audio_gate_session = self.session
        self._gemini_audio_suspended = False
        self._reconnect_event = asyncio.Event()

    def suspend_for_call(self):
        self._gemini_audio_suspended = True
        self._audio_gate_session = self.session
        return True

    def can_restore_after_call(self):
        return self._gemini_audio_suspended and self.session is self._audio_gate_session

    def restore_after_call(self):
        if not self.can_restore_after_call():
            return False
        self._gemini_audio_suspended = False
        return True


def make_calls(tmp_path: Path, *, provider=None, policy=IncomingCallPolicy.NOTIFY_ONLY):
    notifications = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json")
    )
    return CallCoordinator(
        store=CallStore(tmp_path / "calls.json"),
        provider=provider or SimulatedCallProvider(),
        notifications=notifications,
        incoming_policy=policy,
    )


def connect(calls, call_id):
    calls.event(call_id, "ringing", provider_event_id="ring")
    return calls.event(call_id, "connected", provider_event_id="connected")


def test_production_bridge_acquires_and_releases_once_on_terminal_state(tmp_path):
    calls = make_calls(tmp_path, policy=IncomingCallPolicy.ANSWER_SIMULATED)
    gemini = GeminiStub()
    manager = AudioOwnershipManager(gemini=gemini)
    bridge = CallAudioBridge(calls, manager)
    call = calls.simulate_incoming("simulated")

    calls.answer_incoming(call["call_id"])
    assert manager.state is OwnershipState.CALL_AUDIO
    assert bridge.active_call_id == call["call_id"]
    calls.end(call["call_id"])
    assert manager.state is OwnershipState.IDLE
    assert bridge.active_call_id is None
    assert gemini._gemini_audio_suspended is False


def test_duplicate_incoming_event_id_returns_existing_call(tmp_path):
    calls = make_calls(tmp_path)
    first = calls.simulate_incoming("unknown", provider_event_id="incoming-1")
    duplicate = calls.simulate_incoming("unknown-again", provider_event_id="incoming-1")
    assert duplicate["call_id"] == first["call_id"]
    assert len(calls.history()) == 1


def test_invalid_event_does_not_poison_valid_retry(tmp_path):
    calls = make_calls(tmp_path)
    call = calls.request_outgoing("destination", "purpose")
    calls.approve_outgoing(call["call_id"])
    with pytest.raises(ValueError):
        calls.event(call["call_id"], "connected", provider_event_id="delayed-connect")
    calls.event(call["call_id"], "ringing", provider_event_id="ring-1")
    assert calls.event(call["call_id"], "connected", provider_event_id="delayed-connect")["state"] == CallState.CONNECTED.value


def test_owner_stop_handshake_precedes_release():
    manager = AudioOwnershipManager()
    lease = manager.acquire(
        "owner",
        lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream()),
        require_owner_stop=True,
    )
    with pytest.raises(AudioOwnershipError):
        manager.release(lease)
    manager.stop(lease)
    assert manager.release(lease) is True
    assert manager.state is OwnershipState.IDLE


def test_bridge_marks_connected_call_failed_when_audio_acquisition_fails(tmp_path):
    calls = make_calls(tmp_path)
    manager = AudioOwnershipManager(gemini=GeminiStub())
    bridge = CallAudioBridge(calls, manager)
    manager._restoration_failed = True
    call = calls.request_outgoing("destination", "purpose")
    calls.approve_outgoing(call["call_id"])
    connect(calls, call["call_id"])
    assert calls.get(call["call_id"])["state"] == CallState.FAILED.value
    assert manager.state is OwnershipState.IDLE
    assert bridge.errors


def test_attempted_notification_is_deferred_on_restart(tmp_path):
    path = tmp_path / "notifications.json"
    first = ProactiveNotificationCoordinator(
        store=NotificationStore(path), available=lambda: True, voice_send=lambda *_: True
    )
    first.enqueue_event("event", "reminder", "Study")
    assert first.tick()["status"] == "attempted"
    restarted = ProactiveNotificationCoordinator(
        store=NotificationStore(path), available=lambda: False
    )
    assert restarted.store.get("event")["status"] == "deferred"


def test_concurrent_notification_ticks_claim_one_event(tmp_path):
    sent = []
    coordinator = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json"),
        available=lambda: True,
        voice_send=lambda event_id, _text: sent.append(event_id) or True,
    )
    coordinator.enqueue_event("event", "reminder", "Study")
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        coordinator.tick()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    assert sent == ["event"]


def test_dashboard_reports_audio_health_without_private_audio_data(tmp_path):
    dashboard = LocalCommandCenter(
        runtime=None,
        audio_status_reader=lambda: {
            "enabled": True,
            "owner": "call:redacted-id",
            "state": "CALL_AUDIO",
            "healthy": True,
            "errors": {},
        },
    )
    status = dashboard.status()
    assert status["audio"]["state"] == "CALL_AUDIO"
    assert status["audio"]["healthy"] is True
    dashboard.close()
