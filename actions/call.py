"""Authorized action surface for local simulated call coordination only."""
from __future__ import annotations

from automation.call_coordinator import get_default_call_coordinator


TOOL = {
    "name": "call",
    "description": "Manage local simulated call requests and status; never places a real call.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "enum": ["request_outgoing", "approve", "reject", "simulate_incoming", "answer", "end", "cancel", "fail", "list"]},
            "call_id": {"type": "STRING"},
            "destination": {"type": "STRING"},
            "purpose": {"type": "STRING"},
            "reason": {"type": "STRING"},
        },
        "required": ["operation"],
    },
    "handler": lambda parameters, **_: _handle(parameters),
}


def _handle(parameters: dict) -> str:
    coordinator = get_default_call_coordinator()
    operation = str(parameters.get("operation", "")).lower()
    if operation == "request_outgoing":
        record = coordinator.request_outgoing(parameters.get("destination", ""), parameters.get("purpose", ""))
    elif operation == "approve": record = coordinator.approve_outgoing(parameters["call_id"])
    elif operation == "reject": record = coordinator.reject_outgoing(parameters["call_id"])
    elif operation == "simulate_incoming": record = coordinator.simulate_incoming(parameters.get("destination", ""), parameters.get("purpose", "Incoming simulated call"))
    elif operation == "answer": record = coordinator.answer_incoming(parameters["call_id"])
    elif operation == "end": record = coordinator.end(parameters["call_id"])
    elif operation == "cancel": record = coordinator.cancel(parameters["call_id"])
    elif operation == "fail": record = coordinator.fail(parameters["call_id"], parameters.get("reason", "Requested failure"))
    elif operation == "list": return str(coordinator.history())
    else: raise ValueError("Unsupported call operation.")
    return f"Simulated call {record['call_id']} is {record['state']} for {record['destination']} (purpose: {record['purpose']})."
