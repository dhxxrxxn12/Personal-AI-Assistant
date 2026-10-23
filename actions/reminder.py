"""Authorized reminder operations for the voice and local runtime."""
from __future__ import annotations

from automation.reminders import ReminderStore, resolve_request


def _store(parameters: dict) -> ReminderStore:
    path = parameters.get("storage_path")
    return ReminderStore(path) if path else ReminderStore()


def _resolve(parameters: dict):
    timezone_name = parameters.get("timezone")
    if parameters.get("request"):
        return resolve_request(parameters["request"], timezone_name=timezone_name)
    date = str(parameters.get("date") or "").strip()
    clock = str(parameters.get("time") or "").strip()
    message = str(parameters.get("message") or "").strip()
    if not date or not clock or not message:
        raise ValueError("I need a message, date, and time to schedule the reminder.")
    qualifier = " every day" if str(parameters.get("recurrence") or "").lower() == "daily" else ""
    return resolve_request(f"Remind me{qualifier} at {clock} on {date} to {message}", timezone_name=timezone_name)


def reminder(parameters: dict, player=None, **_kwargs) -> str:
    operation = str(parameters.get("operation") or "create").lower().strip()
    store = _store(parameters)
    try:
        if operation == "list":
            records = store.list(include_finished=bool(parameters.get("include_finished")))
            if not records:
                return "You have no active reminders."
            return "\n".join(f"{r['id']}: {r['message']} — {r['next_due']} ({r['status']})" for r in records)
        if operation in {"complete", "cancel"}:
            record = store.mutate(str(parameters.get("reminder_id") or ""), operation)
            label = "completed" if operation == "complete" else "cancelled"
            return f"Reminder {label}: {record['message']}."
        if operation == "reschedule":
            resolved = _resolve(parameters)
            record = store.mutate(str(parameters.get("reminder_id") or ""), operation, due_at=resolved.due_at)
            return f"Reminder rescheduled for {record['next_due']}: {record['message']}."
        if operation != "create":
            return "Choose create, list, complete, cancel, or reschedule."
        record = store.create(_resolve(parameters))
        if player and hasattr(player, "write_log"):
            player.write_log(f"[Reminder] Scheduled {record['next_due']} — {record['message'][:80]}")
        return f"Reminder set for {record['next_due']}: {record['message']}."
    except KeyError:
        return "I couldn't find that reminder."
    except (ValueError, OSError) as exc:
        return str(exc)


TOOL = {
    "name": "reminder",
    "description": "Create, list, complete, cancel, or reschedule local reminders. A due reminder only notifies the user; it never performs the reminded action.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "description": "create, list, complete, cancel, or reschedule"},
            "request": {"type": "STRING", "description": "Natural-language reminder request"},
            "message": {"type": "STRING", "description": "Reminder text"},
            "date": {"type": "STRING", "description": "Date YYYY-MM-DD when normalized"},
            "time": {"type": "STRING", "description": "Time such as 20:00 or 8 PM when normalized"},
            "timezone": {"type": "STRING", "description": "IANA timezone; defaults to the Mac local timezone"},
            "reminder_id": {"type": "STRING", "description": "ID for list-management operations"},
            "include_finished": {"type": "BOOLEAN", "description": "Include completed and cancelled reminders in list"},
            "storage_path": {"type": "STRING", "description": "Testing-only local storage path"},
        },
        "required": [],
    },
    "handler": reminder,
}
