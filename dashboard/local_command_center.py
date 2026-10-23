"""Loopback-only web command center backed by the existing local runtime."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from core.privacy import (
    contains_sensitive_text,
    redact_sensitive_text,
    redact_sensitive_value,
)


BASE_DIR = Path(__file__).resolve().parent.parent
PAGE_PATH = Path(__file__).parent / "static" / "local-command-center.html"
HOST = "127.0.0.1"
PORT = 8765
MAX_GOAL_LENGTH = 2000
ACTIVE_STATES = {"QUEUED", "RUNNING", "APPROVAL_PROCESSING", "AWAITING_PERMISSION"}
def _safe_text(value: Any) -> str:
    return redact_sensitive_text(value)


def _safe_value(value: Any) -> Any:
    return redact_sensitive_value(value)


def is_loopback_peer(address: Optional[str]) -> bool:
    """Return whether an ASGI client address is a loopback IP literal."""
    if not address:
        return False
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _runtime_state(runtime: Any) -> Dict[str, Any]:
    status = runtime.status()
    return status if isinstance(status, dict) else {}


def _state_for_result(result: Dict[str, Any]) -> str:
    state = str(result.get("status") or result.get("state") or "FAILED").upper()
    aliases = {
        "SUCCESS": "COMPLETED",
        "WAITING_FOR_PERMISSION": "AWAITING_PERMISSION",
        "PAUSED": "AWAITING_PERMISSION",
    }
    return aliases.get(state, state)


def _check_request_size(request: Request) -> None:
    raw_length = request.headers.get("content-length", "")
    if not raw_length:
        return
    if not raw_length.isdecimal():
        raise HTTPException(status_code=400, detail="Invalid Content-Length header.")
    if int(raw_length) > 16_384:
        raise HTTPException(status_code=413, detail="Request body is too large.")


class LocalCommandCenter:
    """Coordinates dashboard requests without bypassing runtime authorities."""

    def __init__(
        self,
        *,
        runtime: Any = None,
        runtime_factory: Optional[Callable[[], Any]] = None,
        briefing_reader: Optional[Callable[[], Any]] = None,
        audio_status_reader: Optional[Callable[[], Any]] = None,
        activity_capacity: int = 200,
    ) -> None:
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="jarvis-command-center",
        )
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._idempotency: Dict[str, tuple[str, str]] = {}
        self._active_task_id: Optional[str] = None
        self._events = deque(maxlen=max(1, int(activity_capacity)))
        self._event_seq = 0
        self._briefing_reader = briefing_reader or self._read_briefings
        self._audio_status_reader = audio_status_reader or (lambda: {
            "enabled": False, "state": "UNAVAILABLE", "healthy": True, "owner": None,
        })
        self.runtime = runtime
        self.runtime_error = ""

        if self.runtime is None:
            try:
                self.runtime = (runtime_factory or build_local_runtime)()
            except Exception as exc:
                self.runtime_error = _safe_text(exc)
                logging.getLogger(__name__).error(
                    "Local command center runtime unavailable: %s", self.runtime_error
                )

        if self.runtime is not None:
            restored = _runtime_state(self.runtime)
            goal = str(restored.get("goal") or "").strip()
            state = str(restored.get("status") or restored.get("state") or "").upper()
            if goal and state and state != "IDLE":
                workflow = getattr(self.runtime, "current_workflow", None)
                task_id = f"restored-{uuid.uuid4().hex}"
                self._tasks[task_id] = {
                    "task_id": task_id,
                    "goal": goal,
                    "status": _state_for_result(restored),
                    "result": restored,
                    "error": "",
                    "created_at": getattr(workflow, "created_at", _now()),
                    "updated_at": _now(),
                }
                if self._tasks[task_id]["status"] in ACTIVE_STATES:
                    self._active_task_id = task_id
                self._record_event(
                    "TASK",
                    "Restored the latest local runtime task.",
                    task_id=task_id,
                )
            self._record_event("SYS", "Runtime ready on the local interface.")
        else:
            self._record_event("ERROR", "Runtime is unavailable.", level="error")

    @staticmethod
    def _read_briefings() -> Any:
        from automation.briefing.ledger import BriefingLedger

        return BriefingLedger().runs(limit=5)

    @staticmethod
    def _read_reminders() -> Any:
        from automation.reminders import ReminderStore

        try:
            return ReminderStore().list()
        except (OSError, ValueError):
            return []

    @staticmethod
    def _read_proactive_notifications() -> Any:
        from automation.proactive_notifications import NotificationStore

        try:
            return NotificationStore().list(limit=20)
        except (OSError, ValueError):
            return []

    @staticmethod
    def _read_calls() -> Any:
        from automation.call_coordinator import CallStore

        try:
            return CallStore().list(limit=50)
        except (OSError, ValueError):
            return []

    def _record_event(
        self,
        category: str,
        message: str,
        *,
        task_id: Optional[str] = None,
        level: str = "info",
    ) -> Dict[str, Any]:
        with self._lock:
            self._event_seq += 1
            event = {
                "seq": self._event_seq,
                "category": category,
                "level": level,
                "message": _safe_text(message),
                "data": {"task_id": task_id} if task_id else {},
                "at": _now(),
            }
            self._events.append(event)
            self._changed.notify_all()
            return dict(event)

    def activity(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return [_safe_value(event) for event in list(self._events)[-limit:]]

    def tasks(self) -> list[dict]:
        with self._lock:
            records = list(self._tasks.values())
            return [self._task_payload(task) for task in reversed(records)]

    def task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            item = self._tasks.get(task_id)
            return self._task_payload(item) if item else None

    def _task_payload(self, task: Dict[str, Any]) -> dict:
        workflow = None
        if (
            self.runtime is not None
            and task.get("task_id") == self._active_task_id
            and str(task.get("status")) in ACTIVE_STATES
        ):
            candidate = getattr(self.runtime, "current_workflow", None)
            if candidate is not None and str(getattr(candidate, "goal", "")) == task["goal"]:
                workflow = candidate

        result = task.get("result") or {}
        steps = []
        approvals = []
        completed = 0
        total = 0
        current_step = None
        if workflow is not None:
            total = len(workflow.steps)
            current_step = workflow.current_step
            for step in workflow.steps:
                step_status = str(getattr(step.status, "value", step.status))
                if step_status == "SUCCESS":
                    completed += 1
                safe_result = _safe_value(step.result)
                steps.append({
                    "step_id": _safe_text(step.step_id),
                    "description": _safe_text(step.description),
                    "status": step_status,
                    "result": safe_result,
                    "error": _safe_text(step.error),
                })
                if step_status == "WAITING_PERMISSION":
                    reason = ""
                    if isinstance(step.result, dict):
                        reason = str(step.result.get("reason") or "")
                    approvals.append({
                        "step_id": _safe_text(step.step_id),
                        "action": _safe_text(step.action_id),
                        "description": _safe_text(step.description),
                        "reason": _safe_text(reason),
                    })
        elif isinstance(result.get("workflow"), dict):
            raw_workflow = result["workflow"]
            raw_steps = raw_workflow.get("steps") or []
            total = len(raw_steps)
            for step in raw_steps:
                step_status = str(step.get("status") or "PENDING")
                if step_status == "SUCCESS":
                    completed += 1
                steps.append({
                    "step_id": _safe_text(step.get("step_id")),
                    "description": _safe_text(step.get("description")),
                    "status": step_status,
                    "result": _safe_value(step.get("result")),
                    "error": _safe_text(step.get("error")),
                })
                if step_status == "WAITING_PERMISSION":
                    raw_result = step.get("result")
                    approvals.append({
                        "step_id": _safe_text(step.get("step_id")),
                        "action": _safe_text(step.get("action_id")),
                        "description": _safe_text(step.get("description")),
                        "reason": _safe_text(
                            raw_result.get("reason", "") if isinstance(raw_result, dict) else ""
                        ),
                    })
            current_step = raw_workflow.get("current_step")

        if total == 0:
            total = int(result.get("step_count") or 0)
            completed = len(result.get("completed_steps") or [])

        state = str(task.get("status") or "QUEUED")
        if workflow is not None:
            live_state = str(getattr(workflow.status, "value", workflow.status))
            if live_state == "AWAITING_PERMISSION":
                state = live_state
            elif live_state in {"RUNNING", "RECOVERING", "PAUSED"}:
                state = live_state

        outcome = task.get("error") or result.get("final_result") or result.get("summary") or result
        return {
            "task_id": task["task_id"],
            "goal": _safe_text(task["goal"]),
            "status": state,
            "progress": {
                "completed": completed,
                "total": total,
                "percent": round(100 * completed / total) if total else 0,
                "current_step": _safe_text(current_step) if current_step else None,
            },
            "steps": steps,
            "approvals": approvals,
            "outcome": _safe_value(outcome),
            "error": _safe_text(task.get("error")),
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
        }

    def status(self) -> dict:
        if self.runtime is None:
            return {
                "ready": False,
                "local_only": True,
                "host": HOST,
                "error": self.runtime_error or "Runtime is unavailable.",
                "registered_actions": 0,
                "active_task_id": self._active_task_id,
                "approvals_pending": 0,
                "briefings": [],
                "reminders": self._read_reminders(),
                "proactive_notifications": self._read_proactive_notifications(),
                "calls": self._read_calls(),
                "audio": _safe_value(self._audio_status_reader()),
                "social_summary": None,
            }

        runtime_status = _runtime_state(self.runtime)
        registry = getattr(self.runtime, "action_registry", None)
        actions = registry.names() if registry is not None else ()
        summary = runtime_status.get("summary")
        social_summary = _safe_value(summary) if isinstance(summary, dict) else None
        with self._lock:
            pending = sum(
                len(self._task_payload(task)["approvals"])
                for task in self._tasks.values()
                if task["status"] in ACTIVE_STATES
            )
            active_task_id = self._active_task_id
        return {
            "ready": True,
            "local_only": True,
            "host": HOST,
            "runtime_state": _safe_text(runtime_status.get("state") or "IDLE"),
            "registered_actions": len(actions),
            "active_task_id": active_task_id,
            "approvals_pending": pending,
            "briefings": _safe_value(self._briefing_reader()),
            "reminders": _safe_value(self._read_reminders()),
            "proactive_notifications": _safe_value(self._read_proactive_notifications()),
            "calls": _safe_value(self._read_calls()),
            "audio": _safe_value(self._audio_status_reader()),
            "social_summary": social_summary,
            "error": "",
        }

    def submit_goal(self, goal: str, idempotency_key: str = "") -> tuple[dict, bool]:
        normalized = " ".join(str(goal or "").split())
        if not normalized:
            raise ValueError("Enter a goal before submitting.")
        if contains_sensitive_text(normalized):
            raise ValueError("Goals containing credentials or authentication data are not accepted.")
        if len(normalized) > MAX_GOAL_LENGTH:
            raise ValueError(f"Goals must be {MAX_GOAL_LENGTH} characters or fewer.")
        if self.runtime is None:
            raise RuntimeError(self.runtime_error or "Runtime is unavailable.")

        with self._lock:
            if idempotency_key:
                existing = self._idempotency.get(idempotency_key)
                if existing:
                    existing_goal, existing_task_id = existing
                    if existing_goal != normalized:
                        raise ValueError("That request identifier was already used for a different goal.")
                    return self._task_payload(self._tasks[existing_task_id]), True

            if self._active_task_id:
                active = self._tasks.get(self._active_task_id)
                if active and active["status"] in ACTIVE_STATES:
                    if active["goal"].casefold() == normalized.casefold():
                        return self._task_payload(active), True
                    raise RuntimeError("JARVIS is already processing a goal or waiting for approval.")
                self._active_task_id = None

            task_id = uuid.uuid4().hex
            now = _now()
            task = {
                "task_id": task_id,
                "goal": normalized,
                "status": "QUEUED",
                "result": {},
                "error": "",
                "created_at": now,
                "updated_at": now,
            }
            self._tasks[task_id] = task
            if idempotency_key:
                self._idempotency[idempotency_key] = (normalized, task_id)
            self._active_task_id = task_id
            self._record_event("PLAN", "Goal accepted by the local runtime.", task_id=task_id)
            self._executor.submit(self._execute_goal, task_id, normalized)
            return self._task_payload(task), False

    def _execute_goal(self, task_id: str, goal: str) -> None:
        self._update_task(task_id, "RUNNING")
        self._record_event("TASK", "Runtime execution started.", task_id=task_id)
        try:
            result = self.runtime.run_goal(goal)
            if not isinstance(result, dict):
                raise RuntimeError("The runtime returned an invalid result.")
            state = _state_for_result(result)
            with self._lock:
                task = self._tasks[task_id]
                task["result"] = result
                task["error"] = ""
            self._update_task(task_id, state)
        except Exception as exc:
            safe_error = _safe_text(exc) or "The runtime failed without an error message."
            with self._lock:
                task = self._tasks[task_id]
                task["error"] = safe_error
                task["result"] = {}
            self._update_task(task_id, "FAILED")

        with self._lock:
            final = self._tasks[task_id]["status"]
        category = "PERM" if final == "AWAITING_PERMISSION" else "ERROR" if final in {"FAILED", "BLOCKED"} else "TASK"
        message = {
            "COMPLETED": "Runtime workflow completed.",
            "AWAITING_PERMISSION": "Workflow is waiting for explicit approval.",
            "NEEDS_CLARIFICATION": "Runtime needs clarification before proceeding.",
            "BLOCKED": "Runtime blocked the workflow.",
            "FAILED": "Runtime workflow failed.",
        }.get(final, f"Runtime workflow state: {final}.")
        self._record_event(category, message, task_id=task_id, level="warn" if category in {"PERM", "ERROR"} else "info")

    def approve(self, task_id: str, approved: bool) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError("Task not found.")
            if task["status"] != "AWAITING_PERMISSION":
                raise ValueError("This task is not waiting for runtime permission.")
            payload = self._task_payload(task)
            if not payload["approvals"]:
                raise ValueError("The runtime has no pending permission request for this task.")
            if self.runtime is None:
                raise RuntimeError(self.runtime_error or "Runtime is unavailable.")
            task["status"] = "APPROVAL_PROCESSING"
            task["updated_at"] = _now()
            self._active_task_id = task_id
            decision = "approved" if approved else "denied"
            self._record_event("PERM", f"User {decision} a pending workflow action.", task_id=task_id)
            self._executor.submit(self._resume_workflow, task_id, approved)
            return self._task_payload(task)

    def _resume_workflow(self, task_id: str, approved: bool) -> None:
        self._update_task(task_id, "RUNNING")
        try:
            result = self.runtime.resume(approved=approved)
            if not isinstance(result, dict):
                raise RuntimeError("The runtime returned an invalid result after approval.")
            with self._lock:
                self._tasks[task_id]["result"] = result
                self._tasks[task_id]["error"] = ""
            self._update_task(task_id, _state_for_result(result))
        except Exception as exc:
            with self._lock:
                self._tasks[task_id]["error"] = _safe_text(exc) or "The runtime failed while resuming."
                self._tasks[task_id]["result"] = {}
            self._update_task(task_id, "FAILED")

        with self._lock:
            final = self._tasks[task_id]["status"]
        category = "ERROR" if final in {"FAILED", "BLOCKED"} else "TASK"
        self._record_event(
            category,
            "Approved workflow finished." if approved else "Denied workflow stopped.",
            task_id=task_id,
            level="warn" if category == "ERROR" else "info",
        )

    def _update_task(self, task_id: str, state: str) -> None:
        with self._changed:
            task = self._tasks[task_id]
            task["status"] = state
            task["updated_at"] = _now()
            if state not in ACTIVE_STATES and self._active_task_id == task_id:
                self._active_task_id = None
            self._changed.notify_all()

    def wait_for_task(self, task_id: str, timeout: float = 5.0) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                task = self._tasks.get(task_id)
                if task is None:
                    return None
                if task["status"] not in {"QUEUED", "RUNNING", "APPROVAL_PROCESSING"}:
                    return self._task_payload(task)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._task_payload(task)
                self._changed.wait(remaining)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_local_runtime() -> Any:
    """Create the production runtime using only local state and discovered tools."""
    if os.environ.get("JARVIS_DATA_DIR", "").strip():
        raise RuntimeError(
            "JARVIS_DATA_DIR is set. Unset it before starting the local command center "
            "so runtime state cannot use a configured external data directory."
        )

    from automation.autonomous_runtime import AutonomousRuntime
    from automation.permission_manager import PermissionManager
    from core.action_loader import discover_actions

    registry = discover_actions(
        BASE_DIR / "actions",
        logger=lambda message: logging.getLogger(__name__).info("%s", _safe_text(message)),
    )
    safe_logger = lambda message: logging.getLogger(__name__).info("%s", _safe_text(message))
    return AutonomousRuntime(
        action_registry=registry,
        permissions=PermissionManager(),
        state_path=Path.home() / ".jarvis" / "command_center_runtime.json",
        logger=safe_logger,
    )


def create_app(
    *,
    runtime: Any = None,
    runtime_factory: Optional[Callable[[], Any]] = None,
    briefing_reader: Optional[Callable[[], Any]] = None,
    audio_status_reader: Optional[Callable[[], Any]] = None,
    activity_capacity: int = 200,
) -> FastAPI:
    """Build an isolated FastAPI application for local use and deterministic tests."""
    if runtime is not None and runtime_factory is not None:
        raise ValueError("Pass either runtime or runtime_factory, not both.")
    service = LocalCommandCenter(
        runtime=runtime,
        runtime_factory=runtime_factory,
        briefing_reader=briefing_reader,
        audio_status_reader=audio_status_reader,
        activity_capacity=activity_capacity,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        service.close()

    app = FastAPI(
        title="JARVIS Local Command Center",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.command_center = service

    @app.middleware("http")
    async def restrict_to_loopback(request: Request, call_next):
        peer = request.client.host if request.client else None
        if not is_loopback_peer(peer):
            return JSONResponse(
                {"error": "The local command center accepts loopback connections only."},
                status_code=403,
            )
        try:
            host = urlsplit(f"//{request.headers.get('host', '')}")
            allowed_host = host.hostname in {"127.0.0.1", "localhost"} and host.port == PORT
        except ValueError:
            allowed_host = False
        if not allowed_host:
            return JSONResponse(
                {"error": "The local command center accepts its configured loopback host only."},
                status_code=403,
            )
        origin = request.headers.get("origin")
        if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if origin.rstrip("/") != expected:
                return JSONResponse(
                    {"error": "Cross-origin command requests are not accepted."},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/", response_class=FileResponse)
    async def command_center_page():
        return FileResponse(PAGE_PATH, media_type="text/html; charset=utf-8")

    @app.get("/api/status")
    async def get_status():
        try:
            return service.status()
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Local command center status retrieval failed: %s", _safe_text(exc)
            )
            raise HTTPException(status_code=503, detail=_safe_text(exc)) from exc

    @app.get("/api/tasks")
    async def get_tasks():
        return {"tasks": service.tasks()}

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        task = service.task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return {"task": task}

    @app.get("/api/activity")
    async def get_activity(limit: int = 100):
        if limit < 1 or limit > 200:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 200.")
        return {"events": service.activity(limit)}

    @app.post("/api/goals", status_code=202)
    async def submit_goal(request: Request):
        _check_request_size(request)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("goal"), str):
            raise HTTPException(status_code=422, detail="A text goal is required.")
        goal = payload["goal"]
        if len(goal) > MAX_GOAL_LENGTH:
            raise HTTPException(status_code=422, detail=f"Goals must be {MAX_GOAL_LENGTH} characters or fewer.")
        key = request.headers.get("idempotency-key", "").strip()
        if len(key) > 128:
            raise HTTPException(status_code=400, detail="Idempotency-Key is too long.")
        try:
            task, duplicate = service.submit_goal(goal, key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_safe_text(exc)) from exc
        except RuntimeError as exc:
            status_code = 503 if service.runtime is None else 409
            raise HTTPException(status_code=status_code, detail=_safe_text(exc)) from exc
        return {"task": task, "duplicate": duplicate}

    @app.post("/api/tasks/{task_id}/approval")
    async def decide_approval(task_id: str, request: Request):
        _check_request_size(request)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
        if not isinstance(payload, dict) or type(payload.get("approved")) is not bool:
            raise HTTPException(status_code=422, detail="Approval decision must be true or false.")
        try:
            return {"task": service.approve(task_id, payload["approved"])}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=_safe_text(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=_safe_text(exc)) from exc

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
