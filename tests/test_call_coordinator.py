from pathlib import Path

import pytest

from automation.call_coordinator import (
    CallCoordinator,
    CallState,
    CallStore,
    IncomingCallPolicy,
    SimulatedCallProvider,
)
from automation.permission_manager import PermissionManager, PermissionLevel
from automation.proactive_notifications import NotificationStore, ProactiveNotificationCoordinator


def coordinator(tmp_path: Path, *, policy=IncomingCallPolicy.NOTIFY_ONLY, provider=None):
    notifications = ProactiveNotificationCoordinator(store=NotificationStore(tmp_path / "notifications.json"))
    return CallCoordinator(store=CallStore(tmp_path / "calls.json"), provider=provider or SimulatedCallProvider(),
                           notifications=notifications, incoming_policy=policy)


def test_outgoing_requires_approval_and_has_valid_lifecycle(tmp_path):
    provider = SimulatedCallProvider()
    service = coordinator(tmp_path, provider=provider)
    call = service.request_outgoing("Alice", "discuss study plan")
    assert call["state"] == CallState.PENDING_APPROVAL.value
    assert provider.operations == []
    approved = service.approve_outgoing(call["call_id"])
    assert approved["state"] == CallState.APPROVED.value
    assert provider.operations == [("start_outgoing", call["call_id"])]
    assert service.event(call["call_id"], "ringing", provider_event_id="evt-1")["state"] == "ringing"
    assert service.event(call["call_id"], "connected", provider_event_id="evt-2")["state"] == "connected"
    assert service.end(call["call_id"])["state"] == "ended"


def test_rejection_and_invalid_transition(tmp_path):
    service = coordinator(tmp_path)
    call = service.request_outgoing("Bob", "appointment")
    assert service.reject_outgoing(call["call_id"])["state"] == "rejected"
    with pytest.raises(ValueError):
        service.approve_outgoing(call["call_id"])


def test_incoming_policy_and_answer(tmp_path):
    service = coordinator(tmp_path)
    call = service.simulate_incoming("+91 555", "return a call")
    assert call["state"] == "ringing"
    with pytest.raises(PermissionError):
        service.answer_incoming(call["call_id"])
    enabled = coordinator(tmp_path / "enabled", policy=IncomingCallPolicy.ANSWER_SIMULATED)
    incoming = enabled.simulate_incoming("+91 555")
    assert enabled.answer_incoming(incoming["call_id"])["state"] == "connected"


def test_restart_recovery_and_duplicate_events(tmp_path):
    store = CallStore(tmp_path / "calls.json")
    first = CallCoordinator(store=store, notifications=ProactiveNotificationCoordinator(store=NotificationStore(tmp_path / "n.json")))
    pending = first.request_outgoing("Alice", "study")
    active = first.approve_outgoing(pending["call_id"])
    second = CallCoordinator(store=store, notifications=ProactiveNotificationCoordinator(store=NotificationStore(tmp_path / "n.json")))
    assert second.get(pending["call_id"])["state"] == "failed"
    assert second.event(pending["call_id"], "failed", provider_event_id="same")["state"] == "failed"
    assert second.event(pending["call_id"], "failed", provider_event_id="same")["state"] == "failed"


def test_redaction_cancellation_and_permission(tmp_path):
    service = coordinator(tmp_path)
    call = service.request_outgoing("api_key=SECRET", "use password=TOPSECRET")
    notification = service.notifications.store.list()[0]
    assert "SECRET" not in notification["text"]
    assert "TOPSECRET" not in notification["text"]
    assert service.cancel(call["call_id"])["state"] == "cancelled"
    manager = PermissionManager(path=tmp_path / "permissions.json")
    assert manager.get_permission("call", {"operation": "approve"}) == PermissionLevel.CONFIRM
    assert manager.get_permission("call", {"operation": "request_outgoing"}) == PermissionLevel.SAFE
