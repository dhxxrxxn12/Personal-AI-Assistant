"""Loopback client used by the voice interface to share command-center tasks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, Optional

from core.privacy import contains_sensitive_text, redact_sensitive_text


COMMAND_CENTER_URL = "http://127.0.0.1:8765"
TERMINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "BLOCKED",
    "NEEDS_CLARIFICATION",
    "CANCELLED",
    "AWAITING_PERMISSION",
}


class VoiceCommandCenterError(RuntimeError):
    """Safe error returned when a local voice goal cannot be processed."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


class VoiceCommandCenterClient:
    def __init__(
        self,
        *,
        request_json: Optional[Callable[[str, str, Optional[dict], str], dict]] = None,
        max_wait: float = 25.0,
        poll_interval: float = 0.25,
    ) -> None:
        self._request_json = request_json or self._request
        self._max_wait = max(0.0, float(max_wait))
        self._poll_interval = max(0.05, float(poll_interval))

    async def submit_goal(self, goal: str, call_id: str = "") -> dict:
        normalized = " ".join(str(goal or "").split())
        if not normalized:
            return {"status": "INVALID", "message": "I need a goal before I can start."}
        if contains_sensitive_text(normalized):
            return {
                "status": "REJECTED",
                "message": (
                    "I can't submit a goal containing credentials or authentication "
                    "data. Use the application's secure sign-in flow instead."
                ),
            }

        key_source = str(call_id or uuid.uuid4().hex)
        idempotency_key = "voice-" + hashlib.sha256(key_source.encode("utf-8")).hexdigest()
        try:
            submitted = await asyncio.to_thread(
                self._request_json,
                "POST",
                "/api/goals",
                {"goal": normalized},
                idempotency_key,
            )
        except VoiceCommandCenterError as exc:
            state = (
                "CONFLICT" if exc.status_code == 409
                else "REJECTED" if exc.status_code in {400, 413, 422}
                else "UNAVAILABLE"
            )
            return {"status": state, "message": redact_sensitive_text(exc)}

        task = submitted.get("task") if isinstance(submitted, dict) else None
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id:
            return {
                "status": "FAILED",
                "message": "The local command center returned an invalid task response.",
            }
        return task

    async def wait_for_task(self, task_id: str, initial_task: Optional[dict] = None) -> dict:
        deadline = time.monotonic() + self._max_wait
        latest = initial_task or {"task_id": task_id, "status": "QUEUED"}
        while True:
            state = str(latest.get("status") or "").upper()
            if state in TERMINAL_STATES:
                return latest
            if time.monotonic() >= deadline:
                return latest
            await asyncio.sleep(self._poll_interval)
            try:
                payload = await asyncio.to_thread(
                    self._request_json,
                    "GET",
                    f"/api/tasks/{task_id}",
                    None,
                    "",
                )
            except VoiceCommandCenterError as exc:
                return {
                    "task_id": task_id,
                    "status": "UNKNOWN",
                    "message": redact_sensitive_text(exc),
                }
            latest = payload.get("task") if isinstance(payload, dict) else None
            if not isinstance(latest, dict):
                return {
                    "task_id": task_id,
                    "status": "UNKNOWN",
                    "message": "The local command center could not report this task.",
                }

    async def execute_goal(self, goal: str, call_id: str = "") -> dict:
        task = await self.submit_goal(goal, call_id)
        if task.get("status") in {
            "INVALID", "REJECTED", "CONFLICT", "UNAVAILABLE", "FAILED"
        }:
            return task
        task_id = task.get("task_id")
        if not isinstance(task_id, str):
            return task
        return await self.wait_for_task(task_id, initial_task=task)

    @staticmethod
    def _request(
        method: str,
        path: str,
        payload: Optional[dict],
        idempotency_key: str,
    ) -> dict:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            COMMAND_CENTER_URL + path,
            data=body,
            headers=headers,
            method=method,
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )
        try:
            with opener.open(request, timeout=3.0) as response:
                result = json.loads(response.read(65_536))
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read(16_384))
                detail = error_payload.get("detail") or error_payload.get("error")
            except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
                detail = None
            message = redact_sensitive_text(detail) if detail else (
                "The local command center rejected the request."
            )
            raise VoiceCommandCenterError(message, status_code=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise VoiceCommandCenterError(
                "The local command center is unavailable. Start it at "
                "http://127.0.0.1:8765 and try again."
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise VoiceCommandCenterError(
                "The local command center returned an invalid response."
            ) from None
        if not isinstance(result, dict):
            raise VoiceCommandCenterError(
                "The local command center returned an invalid response."
            )
        return result


def voice_result_text(task: Dict[str, Any]) -> str:
    state = str(task.get("status") or "UNKNOWN").upper()
    task_id = str(task.get("task_id") or "")
    if state == "AWAITING_PERMISSION":
        approvals = task.get("approvals") or []
        action = (
            approvals[0].get("action")
            if approvals and isinstance(approvals[0], dict)
            else ""
        )
        required = f" for {redact_sensitive_text(action)}" if action else ""
        return (
            f"This task needs your approval{required}. It is paused; I have not "
            f"performed the action awaiting approval. Review it in the local "
            f"Command Center at 127.0.0.1:8765. Task {task_id}."
        )
    if state == "COMPLETED":
        outcome = task.get("outcome")
        if isinstance(outcome, str):
            detail = outcome
        elif outcome:
            detail = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
        else:
            detail = "The runtime completed the workflow."
        return "Task completed. " + redact_sensitive_text(detail)[:1200]
    if state in {
        "QUEUED",
        "RUNNING",
        "APPROVAL_PROCESSING",
        "RECOVERING",
    }:
        return (
            f"The task is still {state.lower().replace('_', ' ')}. "
            f"Progress is available in the local Command Center. Task {task_id}."
        )
    detail = task.get("error") or task.get("message") or task.get("outcome")
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    safe_detail = redact_sensitive_text(detail) if detail else ""
    if state == "BLOCKED":
        return "The runtime blocked this task. " + safe_detail[:1000]
    if state == "NEEDS_CLARIFICATION":
        return "I need clarification before I can continue. " + safe_detail[:1000]
    if state == "UNAVAILABLE":
        return safe_detail or "The local command center is unavailable."
    if state == "REJECTED":
        return safe_detail
    if state == "CONFLICT":
        return safe_detail or "Another task is active. Check the local Command Center."
    if state == "INVALID":
        return safe_detail
    return f"The task ended with status {state}. {safe_detail[:1000]}".strip()
