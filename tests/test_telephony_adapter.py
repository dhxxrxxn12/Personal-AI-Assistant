from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from automation.audio_ownership import AudioOwnershipManager, OwnershipState
from automation.call_audio_integration import CallAudioBridge
from automation.call_coordinator import CallCoordinator, CallState, CallStore
from automation.proactive_notifications import NotificationStore, ProactiveNotificationCoordinator
from automation.telephony_adapter import (
    ProviderEventError,
    SimulatedTelephonyAdapter,
)


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


def make_stack(tmp_path: Path, *, frames=None):
    notifications = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json")
    )
    calls = CallCoordinator(
        store=CallStore(tmp_path / "calls.json"),
        notifications=notifications,
    )
    adapter = SimulatedTelephonyAdapter(coordinator=calls, audio_frame_handler=frames)
    calls.provider = adapter
    gemini = GeminiStub()
    manager = AudioOwnershipManager(gemini=gemini)
    bridge = CallAudioBridge(calls, manager)
    return calls, adapter, bridge, manager, gemini


def test_adapter_capabilities_and_outgoing_approval_boundary(tmp_path):
    calls, adapter, bridge, manager, _ = make_stack(tmp_path)
    call = calls.request_outgoing("destination", "purpose")
    assert adapter.operations == []
    assert manager.state is OwnershipState.IDLE

    calls.approve_outgoing(call["call_id"])
    assert adapter.operations == [("start_outgoing", call["call_id"])]
    assert adapter.capabilities().bidirectional_audio is True
    assert adapter.ready() is True
    assert bridge.active_call_id is None


def test_simulated_outgoing_connect_audio_and_hangup_release_once(tmp_path):
    frames = []
    calls, adapter, bridge, manager, gemini = make_stack(tmp_path, frames=frames.append)
    call = calls.request_outgoing("destination", "purpose")
    calls.approve_outgoing(call["call_id"])
    adapter.emit_event({"call_id": call["call_id"], "event_id": "ring-1", "event": "ringing"})
    adapter.emit_event({"call_id": call["call_id"], "event_id": "connect-1", "event": "connected"})
    assert manager.state is OwnershipState.CALL_AUDIO
    assert gemini._gemini_audio_suspended is True

    adapter.emit_audio(call["call_id"], b"pcm", frame_id="frame-1")
    adapter.emit_audio(call["call_id"], b"duplicate", frame_id="frame-1")
    assert len(frames) == 1

    adapter.emit_event({"call_id": call["call_id"], "event_id": "hangup-1", "event": "ended"})
    adapter.emit_event({"call_id": call["call_id"], "event_id": "hangup-1", "event": "ended"})
    assert calls.get(call["call_id"])["state"] == CallState.ENDED.value
    assert manager.state is OwnershipState.IDLE
    assert bridge.active_call_id is None
    assert gemini._gemini_audio_suspended is False


def test_duplicate_incoming_events_create_one_call_and_one_lease(tmp_path):
    calls, adapter, bridge, manager, _ = make_stack(tmp_path)
    first = adapter.receive_incoming("unknown", event_id="incoming-1")
    duplicate = adapter.receive_incoming("unknown", event_id="incoming-1")
    assert first["call_id"] == duplicate["call_id"]
    assert len(calls.history()) == 1
    assert manager.state is OwnershipState.IDLE

    calls.incoming_policy = "answer_simulated"
    calls.answer_incoming(first["call_id"])
    assert manager.state is OwnershipState.CALL_AUDIO
    assert bridge.active_call_id == first["call_id"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"call_id": "id", "event": "connected"},
        {"call_id": "id", "event_id": "e", "event": "unknown"},
    ],
)
def test_malformed_provider_events_are_rejected_before_state_mutation(tmp_path, payload):
    calls, adapter, _, _, _ = make_stack(tmp_path)
    call = calls.request_outgoing("destination", "purpose")
    with pytest.raises(ProviderEventError):
        adapter.emit_event(payload)
    assert calls.get(call["call_id"])["processed_event_ids"] == []


def test_out_of_order_event_can_be_retried_and_audio_is_not_acquired_early(tmp_path):
    calls, adapter, bridge, manager, _ = make_stack(tmp_path)
    call = calls.request_outgoing("destination", "purpose")
    calls.approve_outgoing(call["call_id"])
    with pytest.raises(ValueError):
        adapter.emit_event({"call_id": call["call_id"], "event_id": "connect-late", "event": "connected"})
    assert manager.state is OwnershipState.IDLE
    adapter.emit_event({"call_id": call["call_id"], "event_id": "ring-1", "event": "ringing"})
    adapter.emit_event({"call_id": call["call_id"], "event_id": "connect-late", "event": "connected"})
    assert bridge.active_call_id == call["call_id"]
    assert manager.state is OwnershipState.CALL_AUDIO


def test_adapter_failure_cleans_up_and_does_not_start_audio(tmp_path):
    calls, adapter, bridge, manager, _ = make_stack(tmp_path)
    adapter.set_ready(False)
    call = calls.request_outgoing("destination", "purpose")
    calls.approve_outgoing(call["call_id"])
    assert calls.get(call["call_id"])["state"] == CallState.FAILED.value
    assert manager.state is OwnershipState.IDLE
    assert bridge.active_call_id is None
    assert adapter.operations == []


def test_disconnect_is_provider_failure_boundary_without_persistence_mutation(tmp_path):
    calls, adapter, bridge, manager, _ = make_stack(tmp_path)
    call = calls.request_outgoing("destination", "purpose")
    calls.approve_outgoing(call["call_id"])
    adapter.emit_event({"call_id": call["call_id"], "event_id": "ring-1", "event": "ringing"})
    adapter.emit_event({"call_id": call["call_id"], "event_id": "connect-1", "event": "connected"})
    adapter.disconnect(call["call_id"], event_id="failed-1")
    assert adapter.ready() is False
    assert calls.get(call["call_id"])["state"] == CallState.FAILED.value
    assert manager.state is OwnershipState.IDLE
    assert bridge.active_call_id is None
