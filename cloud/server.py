from __future__ import annotations

import json
import uuid
import asyncio
import logging
import os
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.action_loader import discover_actions
from core.runtime_config import migrate_legacy_file, runtime_path
from automation.autonomous_engine import AutonomousEngine
from automation.autonomous_planner import AutonomousPlanner, PlannerError
from automation.simple_intent_router import SimpleIntentRouter
from automation.self_engineering import (
    collect_diagnostics,
    EngineeringDirector,
    EngineeringPolicy,
    EngineeringTaskStatus,
    GitManager,
    SandboxHandle,
    SandboxManager,
)
from cloud import uploads as upload_store
from cloud.activity_log import activity
from cloud.auth import require_admin, require_agent, validate_login_token
from cloud.device_manager import device_manager
from cloud.engineering import EngineeringTaskStore, redact
from cloud.settings import settings
from cloud.telemetry import health as telemetry_health, telemetry
from cloud.voice import BrowserVoiceSession, voice_tickets

# python-multipart is only needed by the command-center file drop; when it is
# missing the API still starts and /files/upload answers 503.
try:
    from fastapi import File as FastAPIFile, UploadFile
    _UPLOAD_OK = True
except Exception:  # pragma: no cover - depends on optional dependency
    _UPLOAD_OK = False


# ============================================================
# CONFIG
# ============================================================

logger = logging.getLogger("jarvis.cloud")
logging.basicConfig(
    level=getattr(logging, os.getenv("JARVIS_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="JARVIS Cloud",
    version="1.0.0",
    debug=False,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )


@app.middleware("http")
async def request_logging(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "request method=%s path=%s status=%s duration_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

BASE_DIR = Path(__file__).resolve().parent.parent

ACTION_DIR = BASE_DIR / "actions"
CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"  # local-development legacy path
TASK_FILE = settings.task_file
migrate_legacy_file(TASK_FILE, BASE_DIR / "cloud" / "tasks.json")
ENGINEERING_TASK_FILE = runtime_path(
    "engineering_tasks.json",
    BASE_DIR / "cloud" / "engineering_tasks.json",
)
HUD_DIR = BASE_DIR / "dashboard" / "static" / "hud"

# Command center assets are plain ES modules — no build step is required.
# check_dir=False keeps the API importable even if the HUD folder is trimmed.
app.mount(
    "/hud",
    StaticFiles(directory=str(HUD_DIR), check_dir=False),
    name="hud",
)

# A claim is deliberately short-lived.  The Mac agent renews it while a plan is
# running, so a stopped agent cannot leave a task permanently stranded.
LEASE_SECONDS = 60
RECOVERY_SCAN_SECONDS = 10
MAX_SAFE_RECOVERY_ATTEMPTS = 3
MAX_BLOCKED_ACTION_RECOVERY_REPLANS = 3
PLANNING_TIMEOUT_SECONDS = 300
MAX_PLANNING_ATTEMPTS = 3

# "mac" was the original task target, while the registered execution agent
# has always identified itself as "mac-main".  Accept the legacy spelling at
# the API boundary so tasks created by existing clients reach that agent.
DEVICE_ALIASES = {
    "mac": "mac-main",
}


# ============================================================
# HELPERS
# ============================================================

def current_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()


def parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Current writes are UTC-aware. Legacy files contain naive local
        # timestamps, so interpret those in the host timezone before comparing
        # them; this keeps recovery correct across timezone changes.
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return None


def canonical_device_id(device_id: str) -> str:
    """Map legacy task targets to the registered agent identifier."""
    normalized = str(device_id).strip()
    return DEVICE_ALIASES.get(normalized.lower(), normalized)


def seconds_since(value: Any) -> Optional[int]:
    """Age of an ISO timestamp in seconds, or None when unparseable/absent."""
    parsed = parse_time(value)
    if parsed is None:
        return None
    # parse_time normalises offset-aware values to a naive UTC timestamp. Use
    # UTC here too, otherwise a device heartbeat written as +00:00 appears
    # several hours stale on a machine whose local timezone is ahead of UTC.
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    return max(0, int((now_utc - parsed).total_seconds()))


def plan_steps(plan: Any) -> list[Any]:
    """Read plan steps from serialized plans and planner Plan objects."""
    if isinstance(plan, dict):
        steps = plan.get("tasks")
    else:
        steps = getattr(plan, "tasks", None)
    return steps if isinstance(steps, list) else []


def step_value(step: Any, name: str, default: Any = None) -> Any:
    """Read a field from either serialized data or a planner Task object."""
    if isinstance(step, dict):
        return step.get(name, default)
    return getattr(step, name, default)


def step_parameters(step: Any) -> Dict[str, Any]:
    parameters = step_value(step, "parameters", {})
    return dict(parameters) if isinstance(parameters, dict) else {}


# ------------------------------------------------------------
# COMMAND CENTER HELPERS
# ------------------------------------------------------------
# These helpers only format existing state for the HUD activity feed; they never
# change task ownership, leases or the permission gate.

def first_plan_step(plan: Any) -> tuple[Optional[str], Dict[str, Any]]:
    """Return the first planned action and its parameters, if any."""
    steps = plan_steps(plan)
    if not steps:
        return None, {}
    return step_value(steps[0], "action"), step_parameters(steps[0])


def task_execution_target(task: Dict[str, Any]) -> tuple[Optional[str], Dict[str, Any]]:
    """What the agent will run next: an approved continuation, else the plan."""
    execution_plan = task.get("execution_plan")
    steps = plan_steps(execution_plan) or plan_steps(task.get("plan"))
    if not steps:
        return None, {}
    return step_value(steps[0], "action"), step_parameters(steps[0])


def describe_action(action: Optional[str], parameters: Dict[str, Any]) -> str:
    """Compact 'action(detail)' label used in the activity feed."""
    if not action:
        return "unknown action"
    for key in ("app_name", "app", "query", "url", "path", "file_path", "name", "action"):
        value = parameters.get(key)
        if isinstance(value, str) and value.strip():
            return f"{action}({value.strip()[:60]})"
    return str(action)


def task_counts() -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for task in tasks.values():
        status = task.get("status", "unknown")
        summary[status] = summary.get(status, 0) + 1
    return summary


ENGINEERING_EVENT_NAMES = {
    "repository_inspected": "engineering.repository.inspected",
    "capability_analyzed": "engineering.capability.analyzed",
    "plan_created": "engineering.plan.created",
    "inspecting": "engineering.repository.inspected",
    "planning": "engineering.plan.created",
    "approval_required": "engineering.approval.required",
    "sandbox_created": "engineering.sandbox.created",
    "change_started": "engineering.change.started",
    "change_completed": "engineering.change.completed",
    "tests_started": "engineering.tests.started",
    "repair_started": "engineering.repair.started",
    "repair_completed": "engineering.repair.completed",
    "git_checkpoint_created": "engineering.git.checkpoint.created",
    "verification_completed": "engineering.verification.completed",
    "task_failed": "engineering.task.failed",
}


def publish_engineering_event(
    task_id: str,
    event_name: str,
    message: str,
    *,
    level: str = "info",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Use the existing activity bus with a namespaced engineering event."""
    activity.publish(
        "PLAN" if "plan" in event_name or "approval" in event_name else "EXEC",
        message,
        {
            "event": event_name,
            "task_id": task_id,
            **(redact(metadata or {})),
        },
        level=level,
    )


def persist_engineering_result(task_id: str, result: Dict[str, Any]) -> None:
    current = engineering_store.get(task_id)
    if current and current.get("status") == EngineeringTaskStatus.CANCELLED.value:
        return
    engineering_store.put(result)
    for event in result.get("events", []):
        stage = str(event.get("stage", "")).lower()
        event_name = ENGINEERING_EVENT_NAMES.get(stage)
        if event_name:
            publish_engineering_event(
                task_id,
                event_name,
                str(event.get("message", event_name)),
                metadata=event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
            )


async def run_engineering_task(task_id: str, *, approved: bool = False) -> None:
    task = engineering_store.get(task_id)
    if not task or task.get("status") == EngineeringTaskStatus.CANCELLED.value:
        return
    engineering_workers.add(task_id)
    try:
        result = await asyncio.to_thread(
            engine.submit_engineering_goal,
            task["goal"],
            candidate_plan=task.get("candidate_plan"),
            task_id=task_id,
            approved=approved,
            preserve_successful_sandbox=True,
        )
        persist_engineering_result(task_id, result.to_dict())
    except Exception as exc:
        current = engineering_store.get(task_id) or task
        current.update({
            "status": EngineeringTaskStatus.BLOCKED.value,
            "current_phase": EngineeringTaskStatus.BLOCKED.value,
            "error": f"{type(exc).__name__}: {exc}",
            "production_impact": "unchanged",
        })
        engineering_store.put(current)
        publish_engineering_event(
            task_id,
            "engineering.task.failed",
            "Engineering task was blocked before execution.",
            level="error",
            metadata={"error": current["error"]},
        )
    finally:
        engineering_workers.discard(task_id)


def plan_is_safe_to_retry(plan: Any) -> bool:
    """Retry only operations the Mac agent already permits automatically.

    This intentionally mirrors, rather than expands, the agent safety policy.
    An unknown, blocked, or side-effecting action is sent to human review after
    a lost lease instead of being executed a second time.
    """
    steps = plan_steps(plan)
    if not steps:
        return False
    for step in steps:
        action = step_value(step, "action")
        parameters = step_parameters(step)
        if action == "open_app":
            continue
        if (
            action == "browser_control"
            and isinstance(parameters, dict)
            and str(parameters.get("action", "")).lower().strip()
            in {"go_to", "open", "navigate"}
        ):
            continue
        return False
    return True


def blocked_step_from_result(result: Any) -> Optional[tuple[int, Dict[str, Any]]]:
    """Return the first safety-blocked execution result, if one was reported."""
    if not isinstance(result, dict):
        return None
    steps = result.get("steps")
    if not isinstance(steps, list):
        return None
    for index, step in enumerate(steps):
        if (
            isinstance(step, dict)
            and step.get("success") is False
            and step.get("status") == "blocked"
        ):
            return index, step
    return None


def blocked_recovery_context(task: Dict[str, Any], result: Any) -> Optional[Dict[str, Any]]:
    """Build durable, planner-facing context without losing execution evidence."""
    blocked = blocked_step_from_result(result)
    if blocked is None:
        return None

    blocked_index, blocked_result = blocked
    planned_steps = plan_steps(task.get("plan"))

    completed_steps = []
    reported_steps = result.get("steps", [])
    for index, reported_step in enumerate(reported_steps[:blocked_index]):
        if not isinstance(reported_step, dict) or not reported_step.get("success"):
            continue
        planned_step = planned_steps[index] if index < len(planned_steps) else {}
        completed_steps.append({
            "action": step_value(planned_step, "action", reported_step.get("action")),
            "parameters": step_parameters(planned_step),
            "description": step_value(planned_step, "description", ""),
            "result": reported_step.get("result"),
        })

    planned_failure = planned_steps[blocked_index] if blocked_index < len(planned_steps) else {}
    return {
        "original_goal": task.get("goal"),
        "completed_steps": completed_steps,
        "failed_step": {
            "action": blocked_result.get("action", step_value(planned_failure, "action")),
            "parameters": step_parameters(planned_failure),
            "description": step_value(planned_failure, "description", ""),
            "status": blocked_result.get("status"),
            "failure_reason": blocked_result.get("result"),
        },
    }


def permission_request_from_result(
    task: Dict[str, Any],
    result: Any,
    device: str,
    lease_id: str,
) -> Dict[str, Any]:
    """Validate and serialize an agent's request for one exact plan step."""
    if not isinstance(result, dict) or not isinstance(result.get("steps"), list):
        raise ValueError("Permission-required result must contain execution steps.")

    reported_steps = result["steps"]
    matches = [
        (index, step)
        for index, step in enumerate(reported_steps)
        if isinstance(step, dict) and step.get("status") == "permission_required"
    ]
    if len(matches) != 1:
        raise ValueError("Permission-required result must identify exactly one pending step.")

    step_index, reported_step = matches[0]
    if reported_step.get("step_index") != step_index:
        raise ValueError("Permission-required step index does not match the execution record.")

    planned_steps = plan_steps(task.get("plan"))
    if step_index >= len(planned_steps):
        raise ValueError("Permission-required step is not part of the task plan.")
    planned_step = planned_steps[step_index]
    action = step_value(planned_step, "action")
    parameters = step_parameters(planned_step)
    if (
        reported_step.get("action") != action
        or reported_step.get("parameters") != parameters
    ):
        raise ValueError("Permission request does not match the planned action.")

    completed_steps = reported_steps[:step_index]
    if any(
        not isinstance(step, dict) or not step.get("success")
        for step in completed_steps
    ):
        raise ValueError("Permission request cannot skip an incomplete plan step.")

    return {
        "task_id": task["id"],
        "action": action,
        "parameters": parameters,
        "description": step_value(planned_step, "description", ""),
        "step_index": step_index,
        "status": "pending",
        "requested_at": current_time(),
        "requested_by": device,
        "requested_lease_id": lease_id,
    }


async def recover_blocked_action(task: Dict[str, Any], recovery_context: Dict[str, Any]) -> None:
    """Replan a safety-blocked task, retaining the original execution record."""
    attempts = int(task.get("blocked_recovery_attempt_count", 0))
    if attempts >= MAX_BLOCKED_ACTION_RECOVERY_REPLANS:
        task["status"] = "needs_review"
        task["error"] = "Blocked-action recovery attempt limit reached."
        task["recovery_reason"] = task["error"]
        task["completed_at"] = current_time()
        activity.publish(
            "ERROR",
            "Blocked-action recovery limit reached — task needs review",
            {"task_id": task.get("id")},
            level="error",
        )
        return

    task["blocked_recovery_attempt_count"] = attempts + 1
    try:
        replacement = await asyncio.to_thread(
            planner.plan,
            task["goal"],
            False,
            recovery_context,
        )
        replacement_data = replacement if isinstance(replacement, dict) else plan_to_data(replacement)
        if not plan_is_safe_to_retry(replacement_data):
            raise ValueError("Planner did not produce a Mac-safe alternative plan.")
    except Exception as exc:
        task["status"] = "needs_review"
        task["error"] = str(exc)
        task["recovery_reason"] = f"Blocked-action recovery failed: {exc}"
        task["completed_at"] = current_time()
        activity.publish(
            "ERROR",
            f"Blocked-action recovery failed: {exc}",
            {"task_id": task.get("id")},
            level="error",
        )
        return

    task["plan"] = replacement_data
    task["status"] = "planned" if task.get("device") == "cloud" else "queued"
    task["planned_at"] = current_time()
    task["completed_at"] = None
    task["error"] = None
    task["recovery_reason"] = "Blocked action recovered with a Mac-safe alternative plan."
    activity.publish(
        "SYS",
        "Blocked action recovered with a Mac-safe alternative plan",
        {"task_id": task.get("id"), "attempts": task["blocked_recovery_attempt_count"]},
        level="warn",
    )


def recover_expired_leases() -> int:
    """Recover abandoned work without replaying unsafe or ambiguous plans."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recovered = 0
    for task in tasks.values():
        if task.get("status") != "running":
            continue
        expiry = parse_time(task.get("lease_expires_at"))
        # Legacy running tasks have no lease.  Treat them as abandoned rather
        # than preserving an unrecoverable state forever.
        if expiry is not None and expiry > now:
            continue

        attempts = int(task.get("attempt_count", 0))
        if plan_is_safe_to_retry(task.get("plan")) and attempts < MAX_SAFE_RECOVERY_ATTEMPTS:
            task["status"] = "queued"
            task["recovery_reason"] = "Agent lease expired; safe plan requeued."
            task["recovered_at"] = current_time()
        else:
            task["status"] = "needs_review"
            task["recovery_reason"] = (
                "Agent lease expired after work may have started; automatic replay is unsafe."
            )
            task["completed_at"] = current_time()
        task["lease_id"] = None
        task["lease_expires_at"] = None
        recovered += 1
    if recovered:
        save_tasks()
        activity.publish(
            "SYS",
            f"Recovery scan handled {recovered} abandoned lease(s)",
            {"recovered": recovered},
            level="warn",
        )
    return recovered


def plan_to_data(plan: Any) -> Any:
    if is_dataclass(plan):
        return asdict(plan)
    if hasattr(plan, "to_dict"):
        return plan.to_dict()
    if hasattr(plan, "__dict__"):
        return dict(plan.__dict__)
    return str(plan)


def planner_failure_details(error: Exception | str) -> tuple[str, str]:
    """Return a safe planner message/kind for persistence and the HUD."""
    if isinstance(error, PlannerError):
        return error.public_message, error.kind
    if isinstance(error, asyncio.TimeoutError):
        return f"Planning timed out after {PLANNING_TIMEOUT_SECONDS} seconds.", "timeout"
    # Preserve the stable legacy diagnostic used by local callers/tests; do
    # not generalise this into returning arbitrary provider exception text.
    if str(error) == "Gemini planning failed after fallback attempts":
        return str(error), "planner_error"
    return "Gemini planning failed; check Cloud configuration and provider status.", "planner_error"


def fail_task_planning(task: Dict[str, Any], error: Exception | str) -> None:
    """Persist a terminal planner failure without reporting a false success."""
    if task.get("status") != "planning":
        return

    message, kind = planner_failure_details(error)
    task["status"] = "failed"
    task["error"] = message
    task["planner_error"] = kind
    task["plan"] = None
    task["planned_at"] = None
    task["completed_at"] = current_time()
    telemetry_health.mark("gemini", False, message)
    activity.publish(
        "ERROR",
        f"Planning failed: {message}",
        {"task_id": task.get("id"), "goal": task.get("goal"), "kind": kind},
        level="error",
    )
    save_tasks()


async def finish_task_planning(task_id: str) -> None:
    """Route simple safe intents directly; otherwise use the planner worker."""
    task = tasks.get(task_id)
    if task is None or task.get("status") != "planning":
        return

    print(f"[PlannerWorker] Planning task {task_id}")
    plan_source = "gemini"
    try:
        simple_plan = simple_intent_router.route(task["goal"])
        if simple_plan is not None:
            valid, reason = planner.validate_plan(simple_plan.tasks)
            if not valid:
                raise ValueError(f"Simple intent produced an invalid plan: {reason}")
            plan = simple_plan
            plan_source = "simple_intent"
            print(f"[SimpleRouter] Task {task_id} matched deterministic safe intent")
            routed_action, routed_parameters = first_plan_step(plan_to_data(simple_plan))
            activity.publish(
                "PLAN",
                f"Simple intent detected: {describe_action(routed_action, routed_parameters)}",
                {"task_id": task_id, "source": "simple_intent"},
            )
        else:
            plan = await asyncio.wait_for(
                asyncio.to_thread(planner.plan, task["goal"]),
                timeout=PLANNING_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        error = f"Planning timed out after {PLANNING_TIMEOUT_SECONDS} seconds."
        fail_task_planning(task, error)
        print(f"[PlannerWorker] Task {task_id} planning failed: {error}")
        return
    except Exception as exc:
        # A cancellation or another recovery attempt may have changed state
        # while Gemini was running.  Never overwrite that newer state.
        if task.get("status") == "planning":
            fail_task_planning(task, exc)
        message, kind = planner_failure_details(exc)
        print(f"[PlannerWorker] Task {task_id} planning failed kind={kind}: {message}")
        return

    # Do not resurrect a task cancelled while the planner was running.
    if task.get("status") != "planning":
        return

    print(f"[PlannerWorker] Planner returned successfully for task {task_id}")
    plan_data = plan_to_data(plan)
    task["plan"] = plan_data
    task["status"] = "planned" if task.get("device") == "cloud" else "queued"
    task["planned_at"] = current_time()
    task["error"] = None
    plan_step_list = plan_steps(plan_data)
    planned_action, planned_parameters = first_plan_step(plan_data)
    if plan_source == "gemini":
        telemetry_health.mark("gemini", True, "Planner returned a validated plan")
    activity.publish(
        "PLAN",
        (
            f"{'Simple intent' if plan_source == 'simple_intent' else 'Gemini'} plan ready: "
            f"{len(plan_step_list)} step(s) — {describe_action(planned_action, planned_parameters)}"
        ),
        {"task_id": task_id, "source": plan_source, "steps": len(plan_step_list)},
    )
    save_tasks()
    activity.publish(
        "TASK",
        f"Task {task['status']}: {describe_action(planned_action, planned_parameters)}",
        {"task_id": task_id, "status": task["status"], "device": task.get("device")},
    )
    print(
        f"[PlannerWorker] Task {task_id} transitioned planning -> "
        f"{task['status']}"
    )


async def plan_task_in_background(task_id: str) -> None:
    """Finish a newly-created plan without holding up API request handling."""
    print(f"[PlannerWorker] Started task {task_id}")
    try:
        await finish_task_planning(task_id)
    except asyncio.CancelledError:
        print(f"[PlannerWorker] Cancelled task {task_id}")
        raise
    except Exception as exc:
        # This is intentionally a final guard: background task exceptions
        # would otherwise only appear as an unobserved asyncio warning.
        task = tasks.get(task_id)
        if task is not None:
            fail_task_planning(task, exc)
        message, kind = planner_failure_details(exc)
        print(f"[PlannerWorker] Task {task_id} planning failed kind={kind}: {message}")
    finally:
        planning_task_ids.discard(task_id)
        planning_workers.pop(task_id, None)


def start_task_planning(task_id: str) -> bool:
    """Schedule exactly one owned planning worker for a task."""
    if task_id in planning_task_ids:
        return False

    planning_task_ids.add(task_id)
    planning_workers[task_id] = asyncio.create_task(
        plan_task_in_background(task_id),
        name=f"planner-{task_id}",
    )
    return True


async def recover_stalled_planning() -> int:
    """Retry interrupted/transient planning without replaying desktop work."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    candidates = []
    for task_id, task in tasks.items():
        if task.get("status") != "planning":
            continue
        if task_id in planning_task_ids:
            continue
        started = parse_time(task.get("planning_started_at") or task.get("created_at"))
        if started is None or started + timedelta(seconds=PLANNING_TIMEOUT_SECONDS) <= now:
            candidates.append((task_id, task))

    for task_id, task in candidates:
        attempts = int(task.get("planning_attempt_count", 0))
        if attempts >= MAX_PLANNING_ATTEMPTS:
            fail_task_planning(
                task,
                task.get("error") or "Planning retry limit reached.",
            )
            continue

        task["planning_attempt_count"] = attempts + 1
        task["planning_started_at"] = current_time()
        task["error"] = None
        save_tasks()
        planning_task_ids.add(task_id)
        try:
            await finish_task_planning(task_id)
        finally:
            planning_task_ids.discard(task_id)
    return len(candidates)


# ============================================================
# LOAD API CONFIG
# ============================================================

# Production uses GEMINI_API_KEY from the platform secret store. The local
# fallback is intentionally confined to development by cloud.settings.
gemini_api_key = settings.gemini_api_key


# ============================================================
# TASK STORAGE
# ============================================================

tasks: Dict[str, Dict[str, Any]] = {}
TASK_FILE_LOCK = threading.RLock()

# Planning uses the synchronous Gemini SDK.  Keep track of in-process jobs so
# the stalled-planning recovery scan does not start a second plan for a task
# which is already being planned in a worker thread.
planning_task_ids: set[str] = set()
planning_workers: Dict[str, asyncio.Task[None]] = {}


def load_tasks() -> None:
    global tasks

    with TASK_FILE_LOCK:
        TASK_FILE.parent.mkdir(parents=True, exist_ok=True)

        if not TASK_FILE.exists():
            tasks = {}
            return

        try:
            with TASK_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                tasks = data
                # Older persisted tasks predate leases.  Preserve their history but
                # give all future claims the recovery fields expected by this version.
                for task in tasks.values():
                    if not isinstance(task, dict):
                        continue
                    task.setdefault("attempt_count", 0)
                    task.setdefault("lease_id", None)
                    task.setdefault("lease_expires_at", None)
                    task.setdefault("heartbeat_at", None)
                    task.setdefault("completed_by_lease_id", None)
                    task.setdefault("recovery_reason", None)
                    task.setdefault("recovered_at", None)
                    task.setdefault("planning_attempt_count", 0)
                    task.setdefault("planning_started_at", task.get("created_at"))
                    task.setdefault("blocked_recovery_attempt_count", 0)
                    task.setdefault("blocked_recovery_history", [])
                    task.setdefault("permission_request", None)
                    task.setdefault("execution_state", None)
                    task.setdefault("execution_plan", None)
                    task.setdefault("approved_permission", None)
            else:
                tasks = {}

        except Exception as exc:
            logger.error("Could not load tasks: %s", exc)

            tasks = {}


def save_tasks() -> None:
    def make_json_safe(value):
        if value is None or isinstance(
            value,
            (str, int, float, bool),
        ):
            return value

        if hasattr(value, "to_dict"):
            try:
                return make_json_safe(value.to_dict())
            except Exception:
                pass

        if hasattr(value, "__dataclass_fields__"):
            try:
                from dataclasses import asdict
                return make_json_safe(asdict(value))
            except Exception:
                pass

        if isinstance(value, dict):
            return {
                str(key): make_json_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple, set)):
            return [
                make_json_safe(item)
                for item in value
            ]

        return str(value)

    with TASK_FILE_LOCK:
        TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = TASK_FILE.with_suffix(".tmp")
        try:
            safe_tasks = make_json_safe(tasks)

            with temporary_file.open("w", encoding="utf-8") as f:
                json.dump(safe_tasks, f, indent=2, ensure_ascii=False)

            temporary_file.replace(TASK_FILE)

        except Exception as exc:
            logger.error("Could not save tasks: %s", exc)


load_tasks()


# ============================================================
# JARVIS BRAIN
# ============================================================

action_registry = discover_actions(
    ACTION_DIR
)

print(
    f"[JARVIS] Loaded "
    f"{len(action_registry.names())} actions"
)


engine = AutonomousEngine(
    action_registry=action_registry,
    context={
        "platform": "cloud",
    },
    logger=lambda msg: print(
        f"[Autonomous] {msg}"
    ),
)

engineering_director = EngineeringDirector(BASE_DIR)
engine.attach_engineering_director(engineering_director)
engineering_store = EngineeringTaskStore(ENGINEERING_TASK_FILE)
engineering_workers: set[str] = set()


planner = AutonomousPlanner(
    action_registry=action_registry,
    learning_memory=engine.learning,
    api_key=gemini_api_key,
    logger=lambda msg: print(
        f"[Planner] {msg}"
    ),
)


simple_intent_router = SimpleIntentRouter()


# ============================================================
# MODELS
# ============================================================

class TaskRequest(BaseModel):

    goal: str = Field(
        ...,
        min_length=1,
        max_length=5000,
    )

    device: str = Field(
        default="cloud",
        min_length=1,
        max_length=100,
    )


class TaskClaimRequest(BaseModel):

    device: str = Field(
        ...,
        min_length=1,
        max_length=100,
    )


class TaskHeartbeatRequest(BaseModel):

    device: str = Field(..., min_length=1, max_length=100)
    lease_id: str = Field(..., min_length=1, max_length=100)


class TaskResultRequest(BaseModel):

    status: str = Field(
        default="completed",
        min_length=1,
        max_length=50,
    )

    result: Any = None

    error: Optional[str] = None

    # A result is accepted only from the worker that owns the active lease.
    # Retaining this value also makes a retry after a lost HTTP response safe.
    device: str = Field(..., min_length=1, max_length=100)
    lease_id: str = Field(..., min_length=1, max_length=100)


class PermissionApprovalRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=100)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class EngineeringTaskRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=5000)
    candidate_plan: Optional[Dict[str, Any]] = None


class EngineeringApprovalRequest(BaseModel):
    approved: bool = True


class LoginRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=500)


# ============================================================
# ROOT
# ============================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    dashboard_file = BASE_DIR / "dashboard" / "static" / "jarvis-command-center.html"
    return HTMLResponse(dashboard_file.read_text(encoding="utf-8"))


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    login_file = BASE_DIR / "dashboard" / "static" / "login.html"
    return HTMLResponse(login_file.read_text(encoding="utf-8"))

@app.get("/")
async def root():

    return {
        "name": "JARVIS Cloud",
        "version": "1.0.0",
        "status": "online",
        "actions": len(
            action_registry.names()
        ),
        "storage": "persistent",
        "time": current_time(),
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        # Health is a liveness probe. Gemini configuration belongs to the
        # readiness response and must not turn a live HTTP process into a
        # failed platform health check.
        "status": "ok",
        "service": "jarvis-cloud",
        "actions": len(
            action_registry.names()
        ),
        "gemini_configured": bool(gemini_api_key),
        "storage": "persistent",
        "environment": settings.environment,
    }


@app.get("/ready")
async def readiness():
    """Public readiness probe with no task contents or secret values."""
    try:
        TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
        writable = TASK_FILE.parent.is_dir()
    except OSError:
        writable = False
    ready = bool(writable and gemini_api_key)
    return {
        "ready": ready,
        "status": "ready" if ready else "not_ready",
        "storage_writable": writable,
        "gemini_configured": bool(gemini_api_key),
        "auth_configured": bool(settings.admin_token and settings.agent_token),
    }


@app.post("/auth/login")
async def login(request: LoginRequest):
    role = validate_login_token(request.token)
    if role is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"ok": True, "role": role, "token": request.token}


@app.post("/voice/session", dependencies=[Depends(require_admin)])
async def create_voice_session():
    """Issue a one-use ticket for the browser-to-Gemini Live WebSocket."""
    ticket, expires_in = voice_tickets.issue()
    return {"ticket": ticket, "expires_in": expires_in}


@app.websocket("/voice")
async def browser_voice(websocket: WebSocket):
    """Run one ticket-authenticated browser voice session."""
    ticket = websocket.query_params.get("ticket", "")
    if not voice_tickets.consume(ticket):
        await websocket.close(code=4001)
        return

    await websocket.accept()

    async def enqueue_voice_task(goal: str) -> Dict[str, Any]:
        # Voice uses the same task endpoint and permission gate as typed
        # commands.  The Mac Agent is the only execution target.
        return await create_task(TaskRequest(goal=goal, device="mac-main"))

    await BrowserVoiceSession(
        api_key=gemini_api_key,
        submit_task=enqueue_voice_task,
    ).run(websocket)


# ============================================================
# TASK STATUS SUMMARY
# ============================================================

# ============================================================
# DEVICE MANAGEMENT
# ============================================================

class DeviceRegistrationRequest(BaseModel):
    device_id: str
    device_type: str
    name: str
    capabilities: list[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeviceHeartbeatRequest(BaseModel):
    device_id: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


@app.post("/devices/register", dependencies=[Depends(require_agent)])
async def register_device(request: DeviceRegistrationRequest):
    try:
        device = device_manager.register(
            device_id=request.device_id,
            device_type=request.device_type,
            name=request.name,
            capabilities=request.capabilities,
            metadata=request.metadata,
        )
        activity.publish(
            "DEV",
            f"{device['device_id']} registered ({device.get('device_type', 'device')})",
            {
                "device_id": device["device_id"],
                "capabilities": list(device.get("capabilities", [])),
                "metadata": dict(device.get("metadata", {})),
            },
        )
        return {
            "success": True,
            "device": device,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/devices/heartbeat", dependencies=[Depends(require_agent)])
async def device_heartbeat(request: DeviceHeartbeatRequest):
    previous = device_manager.get(request.device_id)
    device = device_manager.heartbeat(
        device_id=request.device_id,
        metadata=request.metadata,
    )

    if device is None:
        raise HTTPException(
            status_code=404,
            detail="Device is not registered",
        )

    # Heartbeats arrive every 15s; only a presence transition is newsworthy.
    if previous is None or previous.get("status") != "online":
        activity.publish(
            "DEV",
            f"{device['device_id']} heartbeat received — online",
            {"device_id": device["device_id"], "status": device.get("status")},
        )

    return {
        "success": True,
        "device": device,
    }


@app.get("/devices", dependencies=[Depends(require_admin)])
async def list_devices():
    return device_manager.status()


@app.get("/devices/{device_id}", dependencies=[Depends(require_admin)])
async def get_device(device_id: str):
    device = device_manager.get(device_id)

    if device is None:
        raise HTTPException(
            status_code=404,
            detail="Device not found",
        )

    return device


@app.get("/tasks/status/summary", dependencies=[Depends(require_admin)])
async def task_summary():

    summary: Dict[str, int] = {}

    for task in tasks.values():

        status = task.get(
            "status",
            "unknown",
        )

        summary[status] = (
            summary.get(
                status,
                0,
            )
            + 1
        )

    return {
        "total": len(tasks),
        "statuses": summary,
    }


# ============================================================
# CONTROLLED SELF-ENGINEERING
# ============================================================

@app.get("/engineering/tasks", dependencies=[Depends(require_admin)])
async def list_engineering_tasks():
    return engineering_store.values()


@app.get("/engineering/diagnostics", dependencies=[Depends(require_admin)])
async def engineering_diagnostics():
    latest = sorted(
        engineering_store.values(),
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )
    test_status = {"status": "not_run"}
    if latest and latest[0].get("test_results"):
        results = latest[0]["test_results"]
        test_status = {
            "status": "passed" if all(item.get("success") for item in results) else "failed",
            "checks": len(results),
        }
    return collect_diagnostics(
        BASE_DIR,
        action_count=len(action_registry.names()),
        test_status=test_status,
    ).to_dict()


@app.post("/engineering/tasks", dependencies=[Depends(require_admin)])
async def create_engineering_task(request: EngineeringTaskRequest):
    task_id = f"engineering-{uuid.uuid4().hex[:12]}"
    task = {
        "task_id": task_id,
        "goal": request.goal.strip(),
        "status": EngineeringTaskStatus.CREATED.value,
        "current_phase": EngineeringTaskStatus.CREATED.value,
        "created_at": current_time(),
        "candidate_plan": redact(request.candidate_plan) if request.candidate_plan else None,
        "required_approval": False,
        "production_impact": "unchanged",
        "events": [{
            "stage": "created",
            "message": "Engineering goal accepted by the authenticated Cloud API.",
            "timestamp": current_time(),
            "metadata": {"event": "engineering.task.created"},
        }],
    }
    engineering_store.put(task)
    publish_engineering_event(
        task_id,
        "engineering.task.created",
        "Engineering task created.",
        metadata={"goal": task["goal"][:160]},
    )
    if not engineering_director.enabled:
        task.update({
            "status": EngineeringTaskStatus.BLOCKED.value,
            "current_phase": EngineeringTaskStatus.BLOCKED.value,
            "error": "Self-engineering is disabled; enable it explicitly for an authenticated engineering run.",
        })
        engineering_store.put(task)
        publish_engineering_event(
            task_id,
            "engineering.task.failed",
            "Self-engineering is disabled by policy.",
            level="warn",
        )
        return task
    asyncio.create_task(run_engineering_task(task_id), name=f"engineering-{task_id}")
    return task


@app.get("/engineering/tasks/{task_id}", dependencies=[Depends(require_admin)])
async def get_engineering_task(task_id: str):
    task = engineering_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Engineering task not found")
    return task


@app.get("/engineering/tasks/{task_id}/events", dependencies=[Depends(require_admin)])
async def get_engineering_task_events(task_id: str):
    task = engineering_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Engineering task not found")
    return {"task_id": task_id, "events": task.get("events", [])}


@app.post("/engineering/tasks/{task_id}/approve", dependencies=[Depends(require_admin)])
async def approve_engineering_task(
    task_id: str,
    request: EngineeringApprovalRequest = EngineeringApprovalRequest(),
):
    task = engineering_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Engineering task not found")
    if not request.approved:
        raise HTTPException(status_code=400, detail="Approval request was not granted")
    if task.get("status") not in {
        EngineeringTaskStatus.AWAITING_APPROVAL.value,
        EngineeringTaskStatus.BLOCKED.value,
        EngineeringTaskStatus.CREATED.value,
    }:
        raise HTTPException(status_code=409, detail="Engineering task is not awaiting approval")
    task["approval_granted_at"] = current_time()
    task["required_approval"] = False
    engineering_store.put(task)
    publish_engineering_event(task_id, "engineering.approval.granted", "Engineering sandbox approval granted.")
    if task_id not in engineering_workers:
        asyncio.create_task(run_engineering_task(task_id, approved=True), name=f"engineering-approve-{task_id}")
    return engineering_store.get(task_id)


@app.post("/engineering/tasks/{task_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_engineering_task(task_id: str):
    task = engineering_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Engineering task not found")
    if task.get("status") in {
        EngineeringTaskStatus.COMPLETED.value,
        EngineeringTaskStatus.ROLLED_BACK.value,
    }:
        raise HTTPException(status_code=409, detail="Completed engineering tasks cannot be cancelled")
    task.update({
        "status": EngineeringTaskStatus.CANCELLED.value,
        "current_phase": EngineeringTaskStatus.CANCELLED.value,
        "completed_at": current_time(),
        "error": "Cancelled by an authenticated administrator.",
        "production_impact": "unchanged",
    })
    engineering_store.put(task)
    publish_engineering_event(task_id, "engineering.task.cancelled", "Engineering task cancelled.", level="warn")
    return task


@app.post("/engineering/tasks/{task_id}/rollback", dependencies=[Depends(require_admin)])
async def rollback_engineering_task(task_id: str):
    task = engineering_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Engineering task not found")
    checkpoint = task.get("git_checkpoint") or {}
    sandbox_path = task.get("sandbox_path")
    commit = checkpoint.get("commit") if isinstance(checkpoint, dict) else None
    if not commit or not sandbox_path:
        raise HTTPException(status_code=409, detail="No preserved sandbox checkpoint is available")
    sandbox = Path(str(sandbox_path)).resolve()
    try:
        sandbox.relative_to(BASE_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Sandbox path is outside the repository boundary") from exc
    if not sandbox.is_dir():
        raise HTTPException(status_code=409, detail="Sandbox is no longer available")
    policy = EngineeringPolicy(sandbox)
    reverted = GitManager(sandbox, policy).rollback(str(commit), approved=True)
    task.update({
        "status": EngineeringTaskStatus.ROLLED_BACK.value,
        "current_phase": EngineeringTaskStatus.ROLLED_BACK.value,
        "rollback_commit": reverted,
        "production_impact": "unchanged",
        "completed_at": current_time(),
    })
    engineering_store.put(task)
    publish_engineering_event(task_id, "engineering.task.rolled_back", "Sandbox checkpoint rolled back; production unchanged.", level="warn")
    return task


# ============================================================
# LIST TASKS
# ============================================================

@app.get("/tasks", dependencies=[Depends(require_admin)])
async def list_tasks():

    return list(
        tasks.values()
    )


# ============================================================
# CREATE TASK
# ============================================================

@app.post("/tasks", dependencies=[Depends(require_admin)])
async def create_task(
    request: TaskRequest,
):

    # Engineering goals use the controlled Director/worktree lifecycle and
    # never enter the Mac action queue as ordinary planner work.
    if engine.is_engineering_goal(request.goal):
        return await create_engineering_task(
            EngineeringTaskRequest(goal=request.goal)
        )

    task_id = str(
        uuid.uuid4()
    )

    print()
    print("=" * 60)

    print(
        f"[JARVIS Cloud] "
        f"New task: {request.goal}"
    )

    print(
        f"[JARVIS Cloud] "
        f"Target device: {request.device}"
    )

    print("=" * 60)

    activity.publish(
        "USER",
        f"Command received: {request.goal[:120]}",
        {"goal": request.goal, "device": canonical_device_id(request.device), "task_id": task_id},
    )
    activity.publish(
        "PLAN",
        "Planning started",
        {"goal": request.goal, "task_id": task_id},
    )

    task = {

        "id": task_id,

        "goal": request.goal,

        "device": canonical_device_id(request.device),

        "status": "planning",

        "plan": None,

        "result": None,

        "error": None,

        "created_at": current_time(),

        "planned_at": None,

        "claimed_at": None,

        "completed_at": None,

        "claimed_by": None,

        # Recovery metadata.  Kept with the task so Cloud restarts preserve
        # the ownership and retry decision rather than guessing from logs.
        "attempt_count": 0,
        "lease_id": None,
        "lease_expires_at": None,
        "heartbeat_at": None,
        "completed_by_lease_id": None,
        "recovery_reason": None,
        "recovered_at": None,
        "planning_attempt_count": 1,
        "planning_started_at": current_time(),
        "blocked_recovery_attempt_count": 0,
        "blocked_recovery_history": [],
        "permission_request": None,
        "execution_state": None,
        "execution_plan": None,
        "approved_permission": None,
    }

    tasks[task_id] = task

    save_tasks()

    # Gemini's SDK and its retry backoff are synchronous.  Calling it from
    # this async endpoint previously stalled Uvicorn's only event loop, which
    # made unrelated device heartbeats and task polls time out.  Persist and
    # return the planning task immediately; completion will queue it for the
    # target agent.
    start_task_planning(task_id)

    return task


# ============================================================
# DEVICE QUEUE
# ============================================================

@app.get(
    "/devices/{device}/tasks",
    dependencies=[Depends(require_agent)],
)
async def get_device_tasks(
    device: str,
):

    return [

        task

        for task in tasks.values()

        if (

            canonical_device_id(task.get("device", ""))
            == canonical_device_id(device)

            and

            task.get(
                "status"
            )
            == "queued"

        )

    ]


# ============================================================
# CLAIM TASK
# ============================================================

@app.post(
    "/tasks/{task_id}/claim",
    dependencies=[Depends(require_agent)],
)
async def claim_task(
    task_id: str,
    request: TaskClaimRequest,
):

    task = tasks.get(
        task_id
    )

    if task is None:

        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    if task.get(
        "status"
    ) != "queued":

        raise HTTPException(
            status_code=409,
            detail=(
                f"Task cannot be claimed. "
                f"Current status: "
                f"{task.get('status')}"
            ),
        )

    if canonical_device_id(task.get("device", "")) != canonical_device_id(request.device):

        raise HTTPException(
            status_code=409,
            detail=(
                f"Task belongs to "
                f"'{task.get('device')}'."
            ),
        )

    task["status"] = "running"

    # Write the canonical identifier once a legacy task is claimed, so its
    # persisted ownership matches the real agent for subsequent heartbeats
    # and result reports.
    task["device"] = canonical_device_id(task.get("device", ""))

    task["claimed_by"] = (
        request.device
    )

    task["claimed_at"] = (
        current_time()
    )

    task["attempt_count"] = int(task.get("attempt_count", 0)) + 1
    task["lease_id"] = str(uuid.uuid4())
    task["lease_expires_at"] = expires_at()
    task["heartbeat_at"] = current_time()
    task["recovery_reason"] = None

    next_action, next_parameters = task_execution_target(task)
    activity.publish(
        "AGENT",
        f"{task['claimed_by']} claimed task",
        {"task_id": task_id, "device": task["claimed_by"], "attempt": task["attempt_count"]},
    )
    activity.publish(
        "EXEC",
        f"Executing {describe_action(next_action, next_parameters)}",
        {"task_id": task_id, "action": next_action, "parameters": next_parameters},
    )

    save_tasks()

    return task


# ============================================================
# RENEW TASK LEASE
# ============================================================

@app.post("/tasks/{task_id}/heartbeat", dependencies=[Depends(require_agent)])
async def heartbeat_task(task_id: str, request: TaskHeartbeatRequest):
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") != "running":
        raise HTTPException(status_code=409, detail="Task is not running")
    if task.get("claimed_by") != request.device or task.get("lease_id") != request.lease_id:
        raise HTTPException(status_code=409, detail="Task lease is not owned by this agent")

    task["heartbeat_at"] = current_time()
    task["lease_expires_at"] = expires_at()
    save_tasks()
    return {"task_id": task_id, "lease_expires_at": task["lease_expires_at"]}


# ============================================================
# GET SINGLE TASK
# ============================================================

@app.get(
    "/tasks/{task_id}",
    dependencies=[Depends(require_admin)],
)
async def get_task(
    task_id: str,
):

    task = tasks.get(
        task_id
    )

    if task is None:

        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return task


# ============================================================
# TASK-SPECIFIC PERMISSIONS
# ============================================================

@app.get("/tasks/{task_id}/permission", dependencies=[Depends(require_admin)])
async def get_task_permission(task_id: str):
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    permission = task.get("permission_request")
    if not isinstance(permission, dict):
        raise HTTPException(status_code=404, detail="Task has no permission request")
    return permission


def matching_permission_request(
    task: Dict[str, Any],
    request: PermissionApprovalRequest,
) -> Dict[str, Any]:
    permission = task.get("permission_request")
    if not isinstance(permission, dict):
        raise HTTPException(status_code=409, detail="Task has no pending permission request")
    if (
        request.action != permission.get("action")
        or request.parameters != permission.get("parameters")
    ):
        raise HTTPException(
            status_code=409,
            detail="Approval does not match the exact pending action and parameters",
        )
    return permission


@app.post("/tasks/{task_id}/permission/approve", dependencies=[Depends(require_admin)])
async def approve_task_permission(task_id: str, request: PermissionApprovalRequest):
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    permission = matching_permission_request(task, request)
    if permission.get("status") == "approved":
        # Retrying the same explicit approval is safe and idempotent.
        return task
    if task.get("status") != "waiting_permission" or permission.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Task is not waiting for this permission")

    planned_steps = plan_steps(task.get("plan"))
    step_index = permission.get("step_index")
    if not isinstance(step_index, int) or step_index < 0 or step_index >= len(planned_steps):
        raise HTTPException(status_code=409, detail="Pending permission step is no longer part of the task plan")

    # Continue from the approved step only. The grant is carried to the agent
    # for this exact action/parameter pair and is never a global allow switch.
    permission["status"] = "approved"
    permission["approved_at"] = current_time()
    task["execution_plan"] = {"tasks": planned_steps[step_index:]}
    task["approved_permission"] = {
        "action": permission["action"],
        "parameters": permission["parameters"],
        "step_index": 0,
    }
    task["status"] = "queued"
    task["claimed_by"] = None
    task["lease_id"] = None
    task["lease_expires_at"] = None
    task["heartbeat_at"] = None
    task["error"] = None
    task["completed_at"] = None
    save_tasks()
    activity.publish(
        "PERM",
        f"Permission approved: {describe_action(permission['action'], dict(permission.get('parameters', {})))}",
        {"task_id": task_id, "action": permission["action"]},
    )
    activity.publish(
        "TASK",
        f"Task requeued for {task.get('device')} after approval",
        {"task_id": task_id, "device": task.get("device")},
    )
    print(f"[Permission] Approved")
    print(f"[Permission] Executing approved action for task {task_id}")
    return task


@app.post("/tasks/{task_id}/permission/deny", dependencies=[Depends(require_admin)])
async def deny_task_permission(task_id: str):
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    permission = task.get("permission_request")
    if not isinstance(permission, dict):
        raise HTTPException(status_code=409, detail="Task has no pending permission request")
    if permission.get("status") == "denied":
        return task
    if task.get("status") != "waiting_permission" or permission.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Task is not waiting for permission")

    permission["status"] = "denied"
    permission["denied_at"] = current_time()
    task["status"] = "needs_review"
    task["error"] = "Permission denied by user."
    task["completed_at"] = current_time()
    task["claimed_by"] = None
    task["lease_id"] = None
    task["lease_expires_at"] = None
    task["heartbeat_at"] = None
    task["approved_permission"] = None
    task["execution_plan"] = None
    save_tasks()
    activity.publish(
        "PERM",
        "Permission denied by user — task needs review",
        {"task_id": task_id, "action": permission.get("action")},
        level="warn",
    )
    print(f"[Permission] Denied task {task_id}")
    return task


# ============================================================
# UPDATE RESULT + LEARNING
# ============================================================

@app.post(
    "/tasks/{task_id}/result",
    dependencies=[Depends(require_agent)],
)
async def update_task_result(
    task_id: str,
    request: TaskResultRequest,
):

    task = tasks.get(
        task_id
    )

    if task is None:

        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    allowed_statuses = {

        "completed",

        "failed",

        "cancelled",

        "permission_required",

    }

    if request.status not in allowed_statuses:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid status. "
                f"Allowed: "
                f"{sorted(allowed_statuses)}"
            ),
        )

    # If the server committed the result but its response was lost, the agent's
    # outbox will submit the same record again.  Accept that exact replay even
    # if blocked-action recovery has already queued a replacement; it must not
    # mutate a newer lease or task state.
    if (
        task.get("completed_by_lease_id") == request.lease_id
        and task.get("claimed_by") == request.device
        and task.get("result") == request.result
    ):
        return task

    # Permission requests are durably acknowledged just like final results,
    # but the task has deliberately released its lease while it waits for the
    # user.  Accept only an exact replay from that released lease.
    if task.get("status") == "waiting_permission":
        permission = task.get("permission_request")
        if (
            request.status == "permission_required"
            and isinstance(permission, dict)
            and permission.get("requested_lease_id") == request.lease_id
            and permission.get("requested_by") == request.device
            and task.get("result") == request.result
        ):
            return task
        raise HTTPException(
            status_code=409,
            detail="Task is already waiting for permission",
        )

    # Never let an old worker overwrite a cancellation or a newer lease.
    if task.get("status") in {"completed", "failed"}:
        if (
            task.get("completed_by_lease_id") == request.lease_id
            and task.get("claimed_by") == request.device
        ):
            return task
        raise HTTPException(status_code=409, detail="Task is already finished")

    if task.get("status") != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Task cannot accept a result. Current status: {task.get('status')}",
        )

    if task.get("claimed_by") != request.device or task.get("lease_id") != request.lease_id:
        raise HTTPException(status_code=409, detail="Task lease is not owned by this agent")

    if request.status == "permission_required":
        try:
            permission = permission_request_from_result(
                task,
                request.result,
                request.device,
                request.lease_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        steps = request.result["steps"]
        task["status"] = "waiting_permission"
        task["result"] = request.result
        task["error"] = None
        task["completed_at"] = None
        task["permission_request"] = permission
        task["execution_state"] = {
            "next_step_index": permission["step_index"],
            "completed_steps": steps[:permission["step_index"]],
            "permission_lease_id": request.lease_id,
        }
        # A user may take arbitrarily long to approve.  Releasing the lease
        # makes this explicit state immune to abandoned-worker recovery.
        task["claimed_by"] = None
        task["lease_id"] = None
        task["lease_expires_at"] = None
        task["heartbeat_at"] = None
        save_tasks()
        permission_action = permission["action"]
        permission_parameters = dict(permission.get("parameters", {}))
        activity.publish(
            "PERM",
            f"Permission required: {describe_action(permission_action, permission_parameters)}",
            {
                "task_id": task_id,
                "action": permission_action,
                "parameters": permission_parameters,
                "step_index": permission.get("step_index"),
            },
            level="warn",
        )
        print("[Permission] Permission required")
        print(f"[Permission] Task: {task_id}")
        print(f"[Permission] Action: {permission['action']}")
        print("[Permission] Waiting for approval")
        print(f"[Permission] Task {task_id} waiting for approval")
        return task

    recovery_context = None
    if request.status == "failed":
        recovery_context = blocked_recovery_context(task, request.result)

    # ----------------------------------------------------------
    # UPDATE RESULT
    # ----------------------------------------------------------

    task["status"] = (
        request.status
    )

    task["result"] = (
        request.result
    )

    task["error"] = (
        request.error
    )

    task["completed_at"] = (
        current_time()
    )
    task["completed_by_lease_id"] = request.lease_id
    task["lease_expires_at"] = None

    # A safety-policy block is distinct from an ordinary execution failure.
    # Preserve its evidence, then attempt a bounded, safe-only replacement
    # plan. The active lease is cleared before planning so a stale worker can
    # never submit a result against a subsequently claimed replacement task.
    if recovery_context is not None:
        history = task.setdefault("blocked_recovery_history", [])
        history.append({
            "at": current_time(),
            "lease_id": request.lease_id,
            "context": recovery_context,
        })
        task["status"] = "recovering"
        task["lease_id"] = None
        await recover_blocked_action(task, recovery_context)
        save_tasks()
        return task

    # ----------------------------------------------------------
    # LEARN SUCCESSFUL WORKFLOW
    # ----------------------------------------------------------
    #
    # The Mac Agent executes the plan, not the Cloud engine.
    # Therefore we learn directly from the successful plan/result
    # reported by the Mac Agent.
    # ----------------------------------------------------------

    if request.status == "completed":

        plan = task.get("plan") or {}
        planned_steps = plan.get("tasks") or []

        # Only learn when the agent actually reported successful
        # execution of the steps.
        reported_result = request.result

        reported_steps = []

        if isinstance(reported_result, dict):
            reported_steps = (
                reported_result.get("steps")
                or []
            )

        successful_execution = True

        if not reported_steps:
            successful_execution = False

        else:
            for reported_step in reported_steps:

                if not isinstance(
                    reported_step,
                    dict,
                ):
                    successful_execution = False
                    break

                if not reported_step.get(
                    "success",
                    False,
                ):
                    successful_execution = False
                    break

        if (
            successful_execution
            and planned_steps
        ):

            try:

                learned_steps = []

                for step in planned_steps:

                    if not isinstance(
                        step,
                        dict,
                    ):
                        continue

                    action = step.get(
                        "action"
                    )

                    if not action:
                        continue

                    learned_steps.append({
                        "action": action,
                        "parameters": dict(
                            step.get(
                                "parameters",
                                {},
                            )
                        ),
                        "description": str(
                            step.get(
                                "description",
                                "",
                            )
                        ),
                    })

                if learned_steps:

                    workflow_name = (
                        "Cloud: "
                        f"{task['goal'][:60]}"
                    )

                    workflow = engine.learning.learn(
                        name=workflow_name,
                        goal=task["goal"],
                        steps=learned_steps,
                    )

                    print(
                        "[JARVIS Cloud] "
                        "🧠 Learned workflow: "
                        f"{workflow.name}"
                    )

                    print(
                        "[JARVIS Cloud] "
                        f"   Steps learned: "
                        f"{len(workflow.steps)}"
                    )

                    print(
                        "[JARVIS Cloud] "
                        f"   Success count: "
                        f"{workflow.success_count}"
                    )

            except Exception as exc:

                print(
                    "[JARVIS Cloud] "
                    f"⚠️ Learning failed: {exc}"
                )

        else:

            print(
                "[JARVIS Cloud] "
                "⚠️ Not learning task because "
                "successful step results were not confirmed."
            )

    # ----------------------------------------------------------
    # SAVE
    # ----------------------------------------------------------

    reported_steps = (
        request.result.get("steps")
        if isinstance(request.result, dict)
        else None
    )
    step_count = len(reported_steps) if isinstance(reported_steps, list) else 0
    if request.status == "completed":
        outcome_level = "info"
    elif request.status == "cancelled":
        outcome_level = "warn"
    else:
        outcome_level = "error"

    activity.publish(
        "EXEC",
        (
            f"Task {request.status}: {str(task.get('goal', ''))[:80]}"
            + (f" — {step_count} step(s)" if step_count else "")
        ),
        {"task_id": task_id, "status": request.status, "steps": step_count},
        level=outcome_level,
    )
    activity.publish(
        "AGENT",
        f"{request.device} reported {request.status}",
        {"task_id": task_id, "device": request.device, "status": request.status},
        level=outcome_level,
    )
    if request.status == "failed":
        activity.publish(
            "ERROR",
            f"Task failed: {task.get('error') or 'see task result'}",
            {"task_id": task_id, "error": task.get("error")},
            level="error",
        )

    save_tasks()

    return task


# ============================================================
# CANCEL TASK
# ============================================================

@app.post(
    "/tasks/{task_id}/cancel",
    dependencies=[Depends(require_admin)],
)
async def cancel_task(
    task_id: str,
):

    task = tasks.get(
        task_id
    )

    if task is None:

        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    if task.get(
        "status"
    ) in {

        "completed",

        "failed",

        "cancelled",

    }:

        raise HTTPException(
            status_code=409,
            detail="Task is already finished.",
        )

    task["status"] = (
        "cancelled"
    )

    task["lease_expires_at"] = None
    task["recovery_reason"] = "Cancelled by user."

    task["completed_at"] = (
        current_time()
    )

    activity.publish(
        "USER",
        f"Task cancelled: {str(task.get('goal', ''))[:80]}",
        {"task_id": task_id},
        level="warn",
    )

    save_tasks()

    return task


# ============================================================
# MEMORY STATUS
# ============================================================

@app.get(
    "/memory/status",
    dependencies=[Depends(require_admin)],
)
async def memory_status():

    return engine.learning.status()


# ============================================================
# ACTIONS
# ============================================================

@app.get(
    "/actions",
    dependencies=[Depends(require_admin)],
)
async def list_actions():

    return {

        "count": len(
            action_registry.names()
        ),

        "actions": action_registry.names(),

    }


# ============================================================
# COMMAND CENTER: HOST TELEMETRY
# ============================================================

@app.get("/system/metrics", dependencies=[Depends(require_admin)])
async def system_metrics():
    """Real metrics for the machine running this Cloud server."""
    return telemetry.sample()


@app.get("/system/status", dependencies=[Depends(require_admin)])
async def system_status():
    """Truthful subsystem state for the HUD's SYSTEM STATUS panel."""
    counts = task_counts()
    devices = device_manager.status()
    device_list = devices.get("devices", [])

    agents = [
        device for device in device_list
        if device.get("device_type") == "mac"
    ]
    online_agents = [device for device in agents if device.get("status") == "online"]

    planner_health = telemetry_health.get("gemini")
    if planner_health is None:
        gemini_ok = bool(gemini_api_key)
        gemini_status = "CONNECTED" if gemini_ok else "UNCONFIGURED"
        gemini_detail = "API key loaded — awaiting first plan"
    else:
        gemini_ok = bool(planner_health.get("ok"))
        gemini_status = "CONNECTED" if gemini_ok else "DEGRADED"
        gemini_detail = planner_health.get("detail") or ""

    queued = counts.get("queued", 0)
    running = counts.get("running", 0)
    planning = counts.get("planning", 0)
    pending_permissions = counts.get("waiting_permission", 0)
    memory_status = engine.learning.status()

    if online_agents:
        agent = sorted(
            online_agents,
            key=lambda device: str(device.get("last_seen", "")),
            reverse=True,
        )[0]
        agent_entry = {
            "key": "mac_agent",
            "label": "Mac Agent",
            "status": "ONLINE",
            "ok": True,
            "detail": f"{agent.get('device_id')} · seen {seconds_since(agent.get('last_seen'))}s ago",
            "data": {
                "device_id": agent.get("device_id"),
                "age_seconds": seconds_since(agent.get("last_seen")),
                "capabilities": list(agent.get("capabilities", [])),
            },
        }
    elif agents:
        agent_entry = {
            "key": "mac_agent",
            "label": "Mac Agent",
            "status": "OFFLINE",
            "ok": False,
            "detail": "registered agent has not sent a heartbeat recently",
            "data": {"device_id": agents[0].get("device_id")},
        }
    else:
        agent_entry = {
            "key": "mac_agent",
            "label": "Mac Agent",
            "status": "NOT REGISTERED",
            "ok": False,
            "detail": "run: python agent/mac_agent.py",
            "data": {},
        }

    return {
        "collected_at": current_time(),
        "subsystems": [
            {
                "key": "cloud",
                "label": "Cloud Server",
                "status": "ONLINE",
                "ok": True,
                "detail": f"FastAPI · {len(tasks)} tasks tracked",
                "data": {"tasks": len(tasks), "actions": len(action_registry.names())},
            },
            {
                "key": "gemini",
                "label": "Gemini API",
                "status": gemini_status,
                "ok": gemini_ok,
                "detail": gemini_detail[:120],
                "data": planner_health or {},
            },
            {
                "key": "task_queue",
                "label": "Task Queue",
                "status": "ACTIVE" if (queued or running or planning) else "IDLE",
                "ok": True,
                "detail": f"{queued} queued · {running} running · {planning} planning",
                "data": {"queued": queued, "running": running, "planning": planning},
            },
            {
                "key": "permission_gate",
                "label": "Permission Gate",
                "status": "ENABLED",
                "ok": True,
                "detail": (
                    f"{pending_permissions} pending request(s) — unsafe actions wait for approval"
                    if pending_permissions
                    else "no pending requests — unsafe actions wait for approval"
                ),
                "data": {"pending": pending_permissions},
            },
            agent_entry,
            {
                "key": "memory",
                "label": "Memory System",
                "status": "ACTIVE",
                "ok": True,
                "detail": f"{memory_status.get('workflow_count', 0)} learned workflow(s)",
                "data": memory_status,
            },
        ],
        "counts": counts,
        "devices": {
            "online": devices.get("online", 0),
            "offline": devices.get("offline", 0),
            "total": devices.get("device_count", 0),
        },
    }


# ============================================================
# COMMAND CENTER: ACTIVITY FEED
# ============================================================

@app.get("/activity", dependencies=[Depends(require_admin)])
async def activity_events(since: int = 0, limit: int = 200):
    """Events published by real Cloud transitions (resume with ?since=<seq>)."""
    bounded = max(1, min(int(limit), 500))
    return {
        "cursor": activity.cursor(),
        "events": activity.since(since, bounded),
    }


@app.get("/activity/stream", dependencies=[Depends(require_admin)])
async def activity_stream(since: int = 0):
    """Server-sent event stream of the same feed (falls back to /activity)."""
    queue = activity.subscribe()
    backlog = activity.since(max(0, int(since)), 200)

    async def event_source():
        try:
            for event in backlog:
                yield f"data: {json.dumps(event)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            activity.unsubscribe(queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# COMMAND CENTER: FILE DROP
# ============================================================

@app.get("/files", dependencies=[Depends(require_admin)])
async def list_files(limit: int = 25):
    """Files previously dropped on the command center."""
    return {
        "directory": str(upload_store.UPLOADS_DIR),
        "files": upload_store.list_uploads(max(1, min(int(limit), 100))),
    }


@app.get("/files/{filename}", dependencies=[Depends(require_admin)])
async def download_file(filename: str):
    path = upload_store.resolve_upload(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=path.name)


if _UPLOAD_OK:

    @app.post("/files/upload", dependencies=[Depends(require_admin)])
    async def upload_file(file: UploadFile = FastAPIFile(...)):
        """Store a dropped file; the Mac agent is what processes it later."""
        limit_bytes = upload_store.MAX_UPLOAD_MB * 1024 * 1024
        destination = upload_store.unique_path(file.filename or "upload")
        total = 0
        try:
            with destination.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File exceeds {upload_store.MAX_UPLOAD_MB} MB limit",
                        )
                    handle.write(chunk)
        except HTTPException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

        activity.publish(
            "USER",
            f"File uploaded: {destination.name}",
            {"name": destination.name, "size": total, "path": str(destination)},
        )
        return {
            "success": True,
            "name": destination.name,
            "size": total,
            "path": str(destination),
        }

else:

    @app.post("/files/upload", dependencies=[Depends(require_admin)])
    async def upload_unavailable():
        raise HTTPException(
            status_code=503,
            detail="File uploads require: pip install python-multipart",
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event(
    "startup"
)
async def startup_event():

    # The activity feed fans out to SSE clients through this loop.
    activity.attach_loop(asyncio.get_running_loop())
    telemetry_health.mark("cloud", True, "Cloud API started")
    telemetry_health.mark(
        "gemini",
        bool(gemini_api_key),
        "API key loaded — awaiting first plan" if gemini_api_key else "GEMINI_API_KEY is not configured",
    )

    # Deal with leases that expired while the service was down before workers
    # can poll again.  A lightweight periodic scan covers later agent crashes.
    recover_expired_leases()

    async def recovery_loop():
        # Do not hold application startup open while an interrupted task is
        # replanned.  Gemini planning runs in a worker thread, allowing API
        # endpoints to become available immediately.
        await recover_stalled_planning()
        while True:
            await asyncio.sleep(RECOVERY_SCAN_SECONDS)
            recover_expired_leases()
            await recover_stalled_planning()

    app.state.recovery_task = asyncio.create_task(recovery_loop())

    print()
    print("=" * 70)
    print(
        "☁️  JARVIS CLOUD STARTED"
    )

    print("=" * 70)

    activity.publish(
        "SYS",
        f"JARVIS cloud online — {len(action_registry.names())} actions loaded",
        {
            "actions": len(action_registry.names()),
            "tasks": len(tasks),
            "storage": str(TASK_FILE),
        },
    )
    activity.publish(
        "SYS",
        f"Plugins loaded: {len(action_registry.names())} actions",
        {"actions": sorted(action_registry.names())},
    )


@app.on_event("shutdown")
async def shutdown_event():
    recovery_task = getattr(app.state, "recovery_task", None)
    if recovery_task:
        recovery_task.cancel()
        await asyncio.gather(recovery_task, return_exceptions=True)

    workers = list(planning_workers.values())
    for worker in workers:
        worker.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)
    planning_workers.clear()
    planning_task_ids.clear()

    print(
        f"Actions: "
        f"{len(action_registry.names())}"
    )

    print(
        f"Persistent tasks: "
        f"{len(tasks)}"
    )

    print(
        f"Task storage: "
        f"{TASK_FILE}"
    )

    print("=" * 70)
