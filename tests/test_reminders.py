from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from actions.reminder import reminder
from automation.reminders import ReminderService, ReminderStore, resolve_request


ZONE = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=ZONE)


def test_natural_language_resolution_and_ambiguity():
    assert resolve_request("Remind me to study at 8 PM", now=NOW, timezone_name="Asia/Kolkata").message == "study"
    tomorrow = resolve_request("Remind me tomorrow at 10 AM to call someone", now=NOW, timezone_name="Asia/Kolkata")
    assert tomorrow.due_at.isoformat().startswith("2026-10-21T10:00:00")
    recurring = resolve_request("Remind me every day at 7 PM to exercise", now=NOW, timezone_name="Asia/Kolkata")
    assert recurring.recurrence == "daily"
    with pytest.raises(ValueError, match="AM or PM"):
        resolve_request("Remind me to study at 8", now=NOW, timezone_name="Asia/Kolkata")


def test_invalid_date_and_timezone_are_reported():
    with pytest.raises(ValueError, match="date is invalid"):
        resolve_request("Remind me on 2026-02-30 at 8 PM to study", now=NOW, timezone_name="Asia/Kolkata")
    with pytest.raises(ValueError, match="Unknown timezone"):
        resolve_request("Remind me tomorrow at 8 PM to study", now=NOW, timezone_name="No/Such_Zone")


def test_persistence_restart_and_one_time_delivery(tmp_path):
    path = tmp_path / "reminders.json"
    store = ReminderStore(path)
    record = store.create(resolve_request("Remind me at 1 PM to study", now=NOW, timezone_name="Asia/Kolkata"))
    restarted = ReminderStore(path)
    delivered = []
    service = ReminderService(restarted, delivered.append)
    assert service.tick(datetime(2026, 10, 20, 13, 1, tzinfo=ZONE))
    assert delivered[0]["message"] == "study"
    assert ReminderService(ReminderStore(path), delivered.append).tick(datetime(2026, 10, 20, 13, 2, tzinfo=ZONE)) == []
    assert ReminderStore(path).list()[0]["status"] == "delivered"


def test_recurring_overdue_advances_once_without_duplicates(tmp_path):
    path = tmp_path / "reminders.json"
    store = ReminderStore(path)
    record = store.create(resolve_request("Remind me every day at 7 PM to exercise", now=NOW, timezone_name="Asia/Kolkata"))
    service = ReminderService(store)
    assert len(service.tick(datetime(2026, 10, 21, 20, tzinfo=ZONE))) == 1
    assert service.tick(datetime(2026, 10, 21, 20, 1, tzinfo=ZONE)) == []
    current = store.list()[0]
    assert current["recurrence"] == "daily"
    assert current["next_due"].startswith("2026-10-22T19:00:00")


def test_management_operations_and_action_confirmation(tmp_path):
    path = tmp_path / "reminders.json"
    result = reminder({"request": "Remind me tomorrow at 8 PM to study", "storage_path": str(path), "timezone": "Asia/Kolkata"})
    assert "Reminder set" in result
    record = ReminderStore(path).list()[0]
    assert "study" in reminder({"operation": "list", "storage_path": str(path)})
    assert "rescheduled" in reminder({"operation": "reschedule", "reminder_id": record["id"], "request": "Remind me tomorrow at 9 PM to study", "storage_path": str(path), "timezone": "Asia/Kolkata"})
    assert "cancelled" in reminder({"operation": "cancel", "reminder_id": record["id"], "storage_path": str(path)})
    assert "cancelled" in reminder({"operation": "list", "include_finished": True, "storage_path": str(path)})


def test_delivery_is_only_a_notification(tmp_path):
    path = tmp_path / "reminders.json"
    store = ReminderStore(path)
    store.create(resolve_request("Remind me at 1 PM to send a message", now=NOW, timezone_name="Asia/Kolkata"))
    calls = []
    ReminderService(store, lambda record: calls.append(record["delivered_message"])).tick(datetime(2026, 10, 20, 13, tzinfo=ZONE))
    assert calls == ["send a message"]


def test_storage_failure_is_reported(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("not json", encoding="utf-8")
    result = reminder({"operation": "list", "storage_path": str(path)})
    assert "unreadable" in result or "corrupt" in result
