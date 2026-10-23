from pathlib import Path

from automation.proactive_notifications import NotificationStore, ProactiveNotificationCoordinator


def test_due_reminder_is_queued_and_delivered_only_after_output_confirmation(tmp_path):
    sent = []
    coordinator = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json"),
        available=lambda: True,
        voice_send=lambda event_id, text: sent.append((event_id, text)) or True,
    )
    event = coordinator.enqueue_reminder({
        "id": "r1", "occurrence": "2026-10-20T14:00:00+05:30", "message": "study",
    })
    assert event["status"] == "queued"
    attempted = coordinator.tick()
    assert attempted["status"] == "attempted"
    assert sent and "study" in sent[0][1]
    assert coordinator.store.get(event["event_id"])["status"] == "attempted"
    delivered = coordinator.mark_output_complete(produced_audio=True)
    assert delivered["status"] == "delivered"


def test_busy_sleeping_or_unavailable_voice_defers_without_loss(tmp_path):
    ready = {"value": False}
    coordinator = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json"),
        available=lambda: ready["value"],
        voice_send=lambda _event_id, _text: True,
    )
    coordinator.enqueue_event("event-1", "reminder", "Take a break")
    assert coordinator.tick() is None
    assert coordinator.store.get("event-1")["status"] == "queued"
    ready["value"] = True
    assert coordinator.tick()["status"] == "attempted"
    coordinator.defer_active("User started speaking")
    assert coordinator.store.get("event-1")["status"] == "deferred"


def test_duplicate_events_are_suppressed_and_restart_recovers_queue(tmp_path):
    path = tmp_path / "notifications.json"
    first = ProactiveNotificationCoordinator(store=NotificationStore(path))
    one = first.enqueue_event("stable-id", "reminder", "Gym")
    duplicate = first.enqueue_event("stable-id", "reminder", "Different text")
    assert duplicate == one
    restarted = NotificationStore(path)
    assert restarted.get("stable-id")["status"] == "queued"


def test_priority_and_backlog_limit_are_deterministic(tmp_path):
    sent = []
    coordinator = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json"),
        available=lambda: True,
        voice_send=lambda event_id, _text: sent.append(event_id) or True,
        max_pending=2,
    )
    coordinator.enqueue_event("ordinary", "event", "ordinary")
    coordinator.enqueue_event("urgent", "event", "urgent", priority="urgent")
    overflow = coordinator.enqueue_event("overflow", "event", "overflow")
    assert overflow["status"] == "dismissed"
    assert coordinator.tick()["event_id"] == "urgent"


def test_failed_output_is_not_reported_as_delivered_and_content_is_redacted(tmp_path):
    coordinator = ProactiveNotificationCoordinator(
        store=NotificationStore(tmp_path / "notifications.json"),
        available=lambda: True,
        voice_send=lambda _event_id, _text: True,
    )
    event = coordinator.enqueue_event("private", "event", "Use password=secret for the task")
    assert "secret" not in event["text"]
    assert coordinator.tick()["status"] == "attempted"
    failed = coordinator.mark_output_complete(produced_audio=False)
    assert failed["status"] == "failed"


def test_cancelled_reminder_invalidates_queued_notification(tmp_path):
    coordinator = ProactiveNotificationCoordinator(store=NotificationStore(tmp_path / "notifications.json"))
    coordinator.enqueue_reminder({"id": "r2", "occurrence": "slot", "message": "call someone"})
    assert coordinator.dismiss_reminder("r2") == 1
    assert coordinator.store.list()[0]["status"] == "dismissed"
