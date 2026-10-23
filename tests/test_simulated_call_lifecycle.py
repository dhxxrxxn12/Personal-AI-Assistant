"""Deterministic Stage 3D tests for the local simulated call lifecycle.

The small bridge in this file represents the future provider/audio adapter.  It
deliberately lives in tests: production CallCoordinator remains independent of
audio ownership and no real call path is introduced by this milestone.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automation.audio_ownership import (
    AudioOwnershipManager,
    FakeAudioStream,
    FakeAudioStreamPair,
    OwnershipBusyError,
    OwnershipState,
)
from automation.call_coordinator import (
    CallCoordinator,
    CallState,
    CallStore,
    IncomingCallPolicy,
    SimulatedCallProvider,
)
from automation.permission_manager import PermissionLevel, PermissionManager
from automation.proactive_notifications import NotificationStore, ProactiveNotificationCoordinator
from dashboard.local_command_center import LocalCommandCenter
from main import JarvisLive


def _jarvis():
    """Build only the lifecycle fields used by the mocked Gemini gate."""
    jarvis = object.__new__(JarvisLive)
    jarvis._audio_gate_enabled = True
    jarvis._audio_gate_session = None
    jarvis._audio_gate_generation = 0
    jarvis._gemini_audio_suspended = False
    jarvis._speaking_lock = threading.Lock()
    jarvis._is_speaking = False
    jarvis._awake = True
    jarvis._interrupted = False
    jarvis._last_user_speech = 0
    jarvis.session = object()
    jarvis._reconnect_event = asyncio.Event()
    jarvis.audio_in_queue = Queue()
    jarvis.ui = SimpleNamespace(muted=False)
    jarvis._proactive_coordinator = Mock()
    jarvis.set_speaking = Mock()
    return jarvis


class _AudioBridge:
    """Test-only adapter: acquire only for connected calls, release on terminal."""

    def __init__(self, coordinator: CallCoordinator, audio: AudioOwnershipManager):
        self.coordinator = coordinator
        self.audio = audio
        self.lease = None

    def sync(self, call_id: str):
        state = self.coordinator.get(call_id)["state"]
        if state == CallState.CONNECTED.value and self.lease is None:
            self.lease = self.audio.acquire(
                f"call:{call_id}",
                lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream()),
            )
        elif state in {
            CallState.ENDED.value,
            CallState.REJECTED.value,
            CallState.FAILED.value,
            CallState.CANCELLED.value,
        } and self.lease is not None:
            self.audio.release(self.lease)
            self.lease = None


def _coordinator(tmp_path: Path, *, policy=IncomingCallPolicy.NOTIFY_ONLY):
    provider = SimulatedCallProvider()
    notifications = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json")
    )
    calls = CallCoordinator(
        store=CallStore(tmp_path / "calls.json"),
        provider=provider,
        notifications=notifications,
        incoming_policy=policy,
    )
    return calls, provider, notifications


def test_incoming_notify_only_does_not_answer_or_acquire_audio(tmp_path):
    calls, provider, _ = _coordinator(tmp_path)
    jarvis = _jarvis()
    bridge = _AudioBridge(calls, AudioOwnershipManager(gemini=jarvis))

    record = calls.simulate_incoming("+91 9999?token=SECRET", "Discuss private plan")
    bridge.sync(record["call_id"])

    assert record["state"] == CallState.RINGING.value
    assert provider.operations == []
    assert bridge.audio.state is OwnershipState.IDLE
    with pytest.raises(PermissionError):
        calls.answer_incoming(record["call_id"])


def test_incoming_explicit_simulated_answer_acquires_audio_and_restores(tmp_path):
    calls, provider, _ = _coordinator(tmp_path, policy=IncomingCallPolicy.ANSWER_SIMULATED)
    jarvis = _jarvis()
    bridge = _AudioBridge(calls, AudioOwnershipManager(gemini=jarvis))

    record = calls.simulate_incoming("simulated-contact")
    calls.answer_incoming(record["call_id"])
    assert provider.operations == [("answer_incoming", record["call_id"])]
    bridge.sync(record["call_id"])
    assert bridge.audio.state is OwnershipState.CALL_AUDIO
    assert jarvis._gemini_audio_suspended is True
    assert jarvis._proactive_voice_available() is False

    calls.end(record["call_id"])
    bridge.sync(record["call_id"])
    assert bridge.audio.state is OwnershipState.IDLE
    assert jarvis._gemini_audio_suspended is False


def test_outgoing_requires_bound_approval_before_provider_or_audio(tmp_path):
    calls, provider, _ = _coordinator(tmp_path)
    jarvis = _jarvis()
    bridge = _AudioBridge(calls, AudioOwnershipManager(gemini=jarvis))
    record = calls.request_outgoing("+91 9876543210", "Call about the project")

    bridge.sync(record["call_id"])
    assert calls.get(record["call_id"])["state"] == CallState.PENDING_APPROVAL.value
    assert provider.operations == []
    assert bridge.audio.state is OwnershipState.IDLE

    approved = calls.approve_outgoing(record["call_id"])
    assert approved["state"] == CallState.APPROVED.value
    assert provider.operations == [("start_outgoing", record["call_id"])]
    calls.event(record["call_id"], "ringing", provider_event_id="ring-1")
    calls.event(record["call_id"], "connected", provider_event_id="connect-1")
    bridge.sync(record["call_id"])
    assert bridge.audio.state is OwnershipState.CALL_AUDIO

    duplicate = calls.event(record["call_id"], "connected", provider_event_id="connect-1")
    assert duplicate["state"] == CallState.CONNECTED.value
    calls.cancel(record["call_id"])
    bridge.sync(record["call_id"])
    assert bridge.audio.state is OwnershipState.IDLE
    assert provider.operations[-1] == ("hangup", record["call_id"])


def test_rejected_cancelled_and_stale_approval_never_start_or_acquire(tmp_path):
    calls, provider, _ = _coordinator(tmp_path)
    bridge = _AudioBridge(calls, AudioOwnershipManager())
    rejected = calls.request_outgoing("contact-a", "purpose-a")
    calls.reject_outgoing(rejected["call_id"])
    with pytest.raises(ValueError):
        calls.approve_outgoing(rejected["call_id"])

    cancelled = calls.request_outgoing("contact-b", "purpose-b")
    calls.cancel(cancelled["call_id"])
    bridge.sync(cancelled["call_id"])
    with pytest.raises(ValueError):
        calls.approve_outgoing(cancelled["call_id"])
    assert provider.operations == []
    assert bridge.audio.state is OwnershipState.IDLE


def test_invalid_order_duplicate_terminal_and_failure_release_audio(tmp_path):
    calls, provider, _ = _coordinator(tmp_path)
    jarvis = _jarvis()
    bridge = _AudioBridge(calls, AudioOwnershipManager(gemini=jarvis))
    record = calls.request_outgoing("destination", "purpose")
    calls.approve_outgoing(record["call_id"])
    with pytest.raises(ValueError):
        calls.event(record["call_id"], "connected", provider_event_id="too-early")
    calls.event(record["call_id"], "ringing", provider_event_id="ring")
    calls.event(record["call_id"], "connected", provider_event_id="connected")
    bridge.sync(record["call_id"])
    calls.fail(record["call_id"], "simulated stream failure")
    bridge.sync(record["call_id"])
    assert calls.get(record["call_id"])["state"] == CallState.FAILED.value
    assert bridge.audio.state is OwnershipState.IDLE
    assert jarvis._gemini_audio_suspended is False
    assert provider.operations == [("start_outgoing", record["call_id"])]


def test_notifications_are_deferred_during_call_audio_and_dashboard_is_redacted(tmp_path, monkeypatch):
    calls, _, notifications = _coordinator(tmp_path)
    jarvis = _jarvis()
    bridge = _AudioBridge(calls, AudioOwnershipManager(gemini=jarvis))
    record = calls.request_outgoing("contact api_key=SECRET", "purpose password=TOPSECRET")
    assert "SECRET" not in notifications.store.get(f"call:{record['call_id']}:pending_approval")["text"]
    assert "TOPSECRET" not in notifications.store.get(f"call:{record['call_id']}:pending_approval")["text"]

    calls.approve_outgoing(record["call_id"])
    calls.event(record["call_id"], "ringing")
    calls.event(record["call_id"], "connected")
    bridge.sync(record["call_id"])
    sent = []
    gated = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "voice.json"),
        available=lambda: not jarvis._gemini_audio_suspended,
        voice_send=lambda event_id, text: sent.append((event_id, text)) or True,
    )
    gated.enqueue(event_id="while-call", kind="briefing", text="Safe notification")
    assert gated.tick() is None
    assert sent == []
    assert gated.store.get("while-call")["status"] == "queued"

    from automation import call_coordinator as call_module
    monkeypatch.setattr(call_module, "CallStore", lambda: CallStore(tmp_path / "calls.json"))
    dashboard = LocalCommandCenter(runtime=None)
    status = dashboard.status()
    assert status["calls"][0]["call_id"] == record["call_id"]
    assert "SECRET" not in str(status)
    assert "TOPSECRET" not in str(status)


def test_permission_manager_keeps_call_approval_confirmation(tmp_path):
    permissions = PermissionManager(path=tmp_path / "permissions.json")
    approval = permissions.check("call", {"operation": "approve", "call_id": "bound-id"})
    answering = permissions.check("call", {"operation": "answer", "call_id": "bound-id"})
    listing = permissions.check("call", {"operation": "list"})
    assert permissions.get_permission("call", {"operation": "approve"}) is PermissionLevel.CONFIRM
    assert approval.requires_confirmation and answering.requires_confirmation
    assert listing.allowed and not listing.requires_confirmation


def test_concurrent_call_audio_requests_have_one_owner_and_shutdown_is_safe(tmp_path):
    calls, _, _ = _coordinator(tmp_path)
    first = calls.request_outgoing("a", "a")
    second = calls.request_outgoing("b", "b")
    calls.approve_outgoing(first["call_id"])
    calls.approve_outgoing(second["call_id"])
    calls.event(first["call_id"], "ringing")
    calls.event(first["call_id"], "connected")
    calls.event(second["call_id"], "ringing")
    calls.event(second["call_id"], "connected")
    manager = AudioOwnershipManager()
    barrier = threading.Barrier(2)
    outcomes = []

    def acquire(owner):
        barrier.wait()
        try:
            outcomes.append(manager.acquire(owner, lambda _cancel: FakeAudioStreamPair(FakeAudioStream(), FakeAudioStream())))
        except OwnershipBusyError:
            outcomes.append(None)

    threads = [threading.Thread(target=acquire, args=("a",)), threading.Thread(target=acquire, args=("b",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    assert sum(item is not None for item in outcomes) == 1
    manager.shutdown()
    assert manager.state is OwnershipState.IDLE


def test_restart_recovery_fails_interrupted_connected_call_without_provider_action(tmp_path):
    store = CallStore(tmp_path / "calls.json")
    provider = SimulatedCallProvider()
    first, _, _ = _coordinator(tmp_path)
    record = first.request_outgoing("destination", "purpose")
    first.approve_outgoing(record["call_id"])
    first.event(record["call_id"], "ringing")
    first.event(record["call_id"], "connected")

    recovered_provider = SimulatedCallProvider()
    recovered = CallCoordinator(
        store=store,
        provider=recovered_provider,
        notifications=ProactiveNotificationCoordinator(
            store=NotificationStore(tmp_path / "recovered-notifications.json")
        ),
        recover=True,
    )
    assert recovered.get(record["call_id"])["state"] == CallState.FAILED.value
    assert recovered_provider.operations == []
