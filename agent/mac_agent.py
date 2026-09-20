import sys
from pathlib import Path

# ---------------------------------------------------------
# PROJECT PATH
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------

import asyncio
import json
import httpx
import os
import platform

from core.action_loader import discover_actions


# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

CLOUD_URL = os.getenv("JARVIS_CLOUD_URL", "http://127.0.0.1:8000").rstrip("/")
AGENT_TOKEN = os.getenv("JARVIS_AGENT_TOKEN", "").strip()
ACTION_DIR = PROJECT_ROOT / "actions"

POLL_INTERVAL = max(1.0, float(os.getenv("JARVIS_AGENT_POLL_SECONDS", "3")))
DEVICE_NAME = os.getenv("JARVIS_AGENT_ID", "mac-main").strip() or "mac-main"
LEASE_HEARTBEAT_INTERVAL = max(5.0, float(os.getenv("JARVIS_AGENT_LEASE_HEARTBEAT_SECONDS", "15")))
OUTBOX_FILE = Path(
    os.getenv("JARVIS_AGENT_OUTBOX_FILE", str(PROJECT_ROOT / "agent" / "mac_agent_outbox.json"))
).expanduser()
HTTP_TIMEOUT = httpx.Timeout(
    connect=float(os.getenv("JARVIS_AGENT_CONNECT_TIMEOUT", "10")),
    read=float(os.getenv("JARVIS_AGENT_READ_TIMEOUT", "30")),
    write=float(os.getenv("JARVIS_AGENT_WRITE_TIMEOUT", "30")),
    pool=float(os.getenv("JARVIS_AGENT_POOL_TIMEOUT", "10")),
)


def request_headers() -> dict[str, str]:
    headers = {}
    if AGENT_TOKEN:
        headers["Authorization"] = f"Bearer {AGENT_TOKEN}"
        headers["X-Jarvis-Device-ID"] = DEVICE_NAME
    return headers


def request_options() -> dict[str, dict[str, str]]:
    """Keep lightweight test clients and development callers backwards-compatible."""
    headers = request_headers()
    return {"headers": headers} if headers else {}


# ---------------------------------------------------------
# AUTOMATIC SAFETY POLICY
# ---------------------------------------------------------
#
# These are actions that can execute without asking the user.
#
# IMPORTANT:
# We do NOT automatically allow every operation of every action.
# Browser actions are checked separately below.
# ---------------------------------------------------------

SAFE_ACTIONS = {
    "open_app",
}


# ---------------------------------------------------------
# SAFE BROWSER OPERATIONS
# ---------------------------------------------------------
#
# Opening/navigating to a browser is currently considered safe.
#
# We intentionally DO NOT automatically allow operations such as:
#
#   click
#   fill
#   submit
#   download
#   upload
#   purchase
#   send
#   login
#
# Those can be added later with proper permission handling.
# ---------------------------------------------------------

SAFE_BROWSER_OPERATIONS = {
    "go_to",
    "open",
    "navigate",
}


# ---------------------------------------------------------
# ACTION REGISTRY
# ---------------------------------------------------------

action_registry = discover_actions(ACTION_DIR)

print(
    f"[Mac Agent] Loaded "
    f"{len(action_registry.names())} actions"
)


# ---------------------------------------------------------
# DURABLE RESULT OUTBOX
# ---------------------------------------------------------
# A task may have completed locally just as the network disappears.  Keep its
# report on disk until Cloud acknowledges it; retrying the same lease/result is
# idempotent on the Cloud side.

def load_outbox():
    try:
        with OUTBOX_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[Mac Agent] Could not load result outbox: {exc}")
        return []


def save_outbox(entries):
    temporary_file = OUTBOX_FILE.with_suffix(".tmp")
    try:
        with temporary_file.open("w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        temporary_file.replace(OUTBOX_FILE)
    except Exception as exc:
        print(f"[Mac Agent] Could not save result outbox: {exc}")


def enqueue_result(task_id, status, result, lease_id):
    entries = load_outbox()
    entry = {
        "task_id": task_id,
        "status": status,
        "result": result,
        "device": DEVICE_NAME,
        "lease_id": lease_id,
    }
    # One result per lease; replacing makes a local retry crash-safe too.
    entries = [item for item in entries if item.get("lease_id") != lease_id]
    entries.append(entry)
    save_outbox(entries)
    return entry


# ---------------------------------------------------------
# PERMISSION CHECK
# ---------------------------------------------------------

def is_action_allowed(
    action,
    parameters,
):
    """
    Decide whether an action may execute automatically.

    Safety is based on BOTH the action and its operation.
    """

    # -----------------------------------------------------
    # NORMAL SAFE ACTIONS
    # -----------------------------------------------------

    if action in SAFE_ACTIONS:
        return True

    # -----------------------------------------------------
    # BROWSER CONTROL
    # -----------------------------------------------------

    if action == "browser_control":

        operation = str(
            parameters.get(
                "action",
                ""
            )
        ).lower().strip()

        if operation in SAFE_BROWSER_OPERATIONS:

            print(
                f"[Mac Agent] "
                f"Safe browser operation allowed: "
                f"{operation}"
            )

            return True

        print(
            f"[Mac Agent] "
            f"Browser operation blocked: "
            f"{operation or '<missing>'}"
        )

        return False

    # -----------------------------------------------------
    # EVERYTHING ELSE
    # -----------------------------------------------------

    return False


# ---------------------------------------------------------
# CLOUD RESULT REPORTING
# ---------------------------------------------------------

async def report_result(
    client,
    entry,
):
    """Send final task result back to JARVIS Cloud."""

    try:

        response = await client.post(
            f"{CLOUD_URL}/tasks/{entry['task_id']}/result",
            **request_options(),
            json={
                "status": entry["status"],
                "result": entry["result"],
                "device": entry["device"],
                "lease_id": entry["lease_id"],
            },
        )

        # A stale lease is intentionally not retried: Cloud may have recovered
        # the work or a user may have cancelled it while this machine was away.
        if response.status_code in {404, 409}:
            print(f"[Mac Agent] Discarding stale result: {response.status_code}")
            return True

        response.raise_for_status()

        print(
            f"[Mac Agent] Cloud updated: "
            f"{response.status_code}"
        )
        return True

    except Exception as e:

        print(
            f"[Mac Agent] Failed to report result: "
            f"{e}"
        )
        return False


async def flush_outbox(client):
    """Retry every durable result before requesting more work."""
    entries = load_outbox()
    remaining = []
    for entry in entries:
        required = {"task_id", "status", "result", "device", "lease_id"}
        if not isinstance(entry, dict) or not required.issubset(entry):
            print("[Mac Agent] Discarding malformed outbox entry")
            continue
        if not await report_result(client, entry):
            remaining.append(entry)
    if len(remaining) != len(entries):
        save_outbox(remaining)


async def renew_lease(client, task_id, lease_id, stop_event):
    """Keep a claim alive while synchronous desktop work runs in a thread."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=LEASE_HEARTBEAT_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
        try:
            response = await client.post(
                f"{CLOUD_URL}/tasks/{task_id}/heartbeat",
                **request_options(),
                json={"device": DEVICE_NAME, "lease_id": lease_id},
            )
            if response.status_code == 409:
                print("[Mac Agent] Lease is no longer active; result will be discarded.")
                return
            response.raise_for_status()
        except Exception as exc:
            # Keep executing.  The outbox retains the final report and a later
            # heartbeat may succeed before the 60-second lease expires.
            print(f"[Mac Agent] Heartbeat failed: {exc}")


# ---------------------------------------------------------
# CLAIM TASK
# ---------------------------------------------------------

async def claim_task(
    client,
    task_id,
):
    """
    Ask JARVIS Cloud to atomically assign this task
    to this Mac agent.
    """

    try:

        response = await client.post(
            f"{CLOUD_URL}/tasks/{task_id}/claim",
            **request_options(),
            json={
                "device": DEVICE_NAME,
            },
        )

        if response.status_code == 200:

            task = response.json()

            print(
                f"[Mac Agent] Task claimed: "
                f"{task_id}"
            )

            return task

        if response.status_code == 409:

            print(
                f"[Mac Agent] Task already claimed: "
                f"{task_id}"
            )

            return None

        print(
            f"[Mac Agent] Claim failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

        return None

    except Exception as e:

        print(
            f"[Mac Agent] Claim error: {e}"
        )

        return None


# ---------------------------------------------------------
# EXECUTE ONE ACTION
# ---------------------------------------------------------

def execute_action(
    action,
    parameters,
    approved_permission=None,
):
    """Execute one registered JARVIS action."""

    print(
        f"[Mac Agent] Executing: {action}"
    )

    print(
        f"[Mac Agent] Parameters: "
        f"{parameters}"
    )

    # -----------------------------------------------------
    # ACTION EXISTS?
    # -----------------------------------------------------

    if not action_registry.has(action):

        print(
            f"[Mac Agent] UNKNOWN ACTION: "
            f"{action}"
        )

        return {
            "success": False,
            "action": action,
            "status": "unknown_action",
            "result": (
                f"Action '{action}' "
                f"does not exist."
            ),
        }

    # -----------------------------------------------------
    # SAFETY CHECK
    # -----------------------------------------------------

    automatically_allowed = is_action_allowed(action, parameters)
    explicitly_approved = (
        isinstance(approved_permission, dict)
        and approved_permission.get("action") == action
        and approved_permission.get("parameters") == parameters
    )

    if not automatically_allowed and not explicitly_approved:

        print("[Permission] Permission required")
        print(f"[Permission] Action: {action}")

        print(
            f"[Mac Agent] BLOCKED: "
            f"{action}"
        )

        return {
            "success": False,
            "action": action,
            "status": "permission_required",
            "parameters": parameters,
            "result": (
                f"Action '{action}' "
                f"is not allowed automatically."
            ),
        }

    if explicitly_approved:
        print("[Permission] Executing approved action")

    # -----------------------------------------------------
    # EXECUTE
    # -----------------------------------------------------

    try:

        result = action_registry.run(
            action,
            parameters,
        )

        print(
            f"[Mac Agent] Result: {result}"
        )

        failed_result = (
            isinstance(result, str)
            and (
                result.startswith("Could not ")
                or result.startswith("Error:")
                or result.startswith("Failed:")
            )
        )

        return {
            "success": not failed_result,
            "action": action,
            "status": "completed" if not failed_result else "execution_failed",
            "result": str(result),
        }

    except Exception as e:

        print(
            f"[Mac Agent] Execution failed: "
            f"{e}"
        )

        return {
            "success": False,
            "action": action,
            "status": "execution_failed",
            "result": str(e),
        }


# ---------------------------------------------------------
# EXECUTE COMPLETE PLAN
# ---------------------------------------------------------

def execute_plan(plan, approved_permission=None):
    """
    Execute every step in the generated plan sequentially.

    Stops immediately if a step fails or is blocked.
    """

    planned_tasks = plan.get(
        "tasks",
        [],
    )

    if not planned_tasks:

        return {
            "success": False,
            "status": "failed",
            "results": [
                {
                    "success": False,
                    "status": "failed",
                    "result": "Plan contains no tasks.",
                }
            ],
        }

    results = []

    print(
        f"[Mac Agent] Executing "
        f"{len(planned_tasks)} step(s)"
    )

    # -----------------------------------------------------
    # STEP-BY-STEP EXECUTION
    # -----------------------------------------------------

    for index, step in enumerate(
        planned_tasks,
    ):

        action = step.get(
            "action"
        )

        parameters = step.get(
            "parameters",
            {},
        )

        description = step.get(
            "description",
            "",
        )

        print()
        print(
            "----------------------------------------"
        )

        print(
            f"[Mac Agent] STEP "
            f"{index + 1}/{len(planned_tasks)}"
        )

        print(
            f"[Mac Agent] Action: {action}"
        )

        if description:

            print(
                f"[Mac Agent] "
                f"Description: {description}"
            )

        # -------------------------------------------------
        # INVALID STEP
        # -------------------------------------------------

        if not action:

            step_result = {
                "success": False,
                "action": None,
                "status": "invalid_step",
                "result": "No action specified.",
            }

        else:

            step_result = execute_action(
                action,
                parameters,
                approved_permission=approved_permission
                if index == 0 else None,
            )

        step_result["step_index"] = index
        step_result.setdefault("parameters", parameters)

        results.append(
            step_result
        )

        # -------------------------------------------------
        # STOP ON FAILURE
        # -------------------------------------------------

        if not step_result["success"]:

            print(
                f"[Mac Agent] "
                f"STEP {index + 1} FAILED"
            )

            print(
                "[Mac Agent] "
                "Stopping remaining steps."
            )

            return {
                "success": False,
                "status": step_result.get("status", "failed"),
                "results": results,
            }

        print(
            f"[Mac Agent] "
            f"STEP {index + 1} COMPLETED"
        )

    # -----------------------------------------------------
    # ALL STEPS COMPLETED
    # -----------------------------------------------------

    print()
    print(
        "[Mac Agent] ALL STEPS COMPLETED"
    )

    return {
        "success": True,
        "status": "completed",
        "results": results,
    }


# ---------------------------------------------------------
# DEVICE REGISTRATION + HEARTBEAT
# ---------------------------------------------------------

DEVICE_TYPE = "mac"
DEVICE_CAPABILITIES = [
    "open_app",
    "browser_control",
]


async def register_device(client):
    """Register this Mac Agent with JARVIS Cloud."""
    payload = {
        "device_id": DEVICE_NAME,
        "device_type": DEVICE_TYPE,
        "name": "JARVIS Mac Agent",
        "capabilities": DEVICE_CAPABILITIES,
        "metadata": {
            "os": sys.platform,
            "architecture": platform.machine(),
        },
    }

    response = await client.post(
        f"{CLOUD_URL}/devices/register",
        **request_options(),
        json=payload,
    )
    response.raise_for_status()

    print(
        f"[Mac Agent] Device registered: {DEVICE_NAME}"
    )


async def send_device_heartbeat(client):
    """Tell Cloud that this Mac Agent is still online."""
    payload = {
        "device_id": DEVICE_NAME,
        "metadata": {
            "os": sys.platform,
            "architecture": platform.machine(),
        },
    }

    response = await client.post(
        f"{CLOUD_URL}/devices/heartbeat",
        **request_options(),
        json=payload,
    )
    response.raise_for_status()


async def device_heartbeat_loop(client, stop_event):
    """Keep the device presence alive while the agent is running."""
    while not stop_event.is_set():
        try:
            await send_device_heartbeat(client)
        except Exception as exc:
            print(
                f"[Mac Agent] Device heartbeat failed: {type(exc).__name__}: {exc!r}"
            )

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=LEASE_HEARTBEAT_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------
# MAIN TASK LOOP
# ---------------------------------------------------------

async def get_tasks():

    print()
    print(
        "========================================"
    )

    print(
        "          JARVIS MAC AGENT"
    )

    print(
        "========================================"
    )

    print(
        f"[Mac Agent] Cloud: {CLOUD_URL}"
    )

    print(
        f"[Mac Agent] Actions loaded: "
        f"{len(action_registry.names())}"
    )

    print(
        f"[Mac Agent] Automatic actions: "
        f"{', '.join(sorted(SAFE_ACTIONS))}"
    )

    print(
        "[Mac Agent] Safe browser operations: "
        f"{', '.join(sorted(SAFE_BROWSER_OPERATIONS))}"
    )

    print(
        f"[Mac Agent] Poll interval: "
        f"{POLL_INTERVAL}s"
    )

    print(
        "========================================"
    )

    print()

    print(
        "[Mac Agent] Connecting to JARVIS Cloud..."
    )

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        headers=request_headers(),
    ) as client:

        print(
            "[Mac Agent] Connected to JARVIS Cloud"
        )

        # -------------------------------------------------
        # DEVICE REGISTRATION
        # -------------------------------------------------

        registered = False
        try:
            await register_device(client)
            registered = True
        except Exception as exc:
            print(f"[Mac Agent] Device registration failed: {type(exc).__name__}")

        # -------------------------------------------------
        # DEVICE HEARTBEAT
        # -------------------------------------------------

        heartbeat_stop = asyncio.Event()

        heartbeat_task = asyncio.create_task(
            device_heartbeat_loop(
                client,
                heartbeat_stop,
            )
        )

        print(
            "[Mac Agent] Device registration + heartbeat started"
        )

        print(
            "[Mac Agent] Waiting for tasks..."
        )

        while True:

            try:

                # Registration is retried after a network outage or a Cloud
                # restart. The Cloud remains authoritative for device state.
                if not registered:
                    await register_device(client)
                    registered = True

                # A prior execution may have finished after Cloud became
                # unreachable.  Deliver it before claiming additional work.
                await flush_outbox(client)

                # -------------------------------------------------
                # GET MAC QUEUE
                # -------------------------------------------------

                response = await client.get(
                    f"{CLOUD_URL}/devices/{DEVICE_NAME}/tasks",
                    **request_options(),
                )

                response.raise_for_status()

                tasks = response.json()

                # -------------------------------------------------
                # PROCESS TASKS
                # -------------------------------------------------

                for queued_task in tasks:

                    task_id = queued_task.get(
                        "id"
                    )

                    if not task_id:
                        continue

                    print()
                    print(
                        "========================================"
                    )

                    print(
                        "[Mac Agent] NEW TASK"
                    )

                    print(
                        f"[Mac Agent] ID: {task_id}"
                    )

                    print(
                        f"[Mac Agent] Goal: "
                        f"{queued_task.get('goal')}"
                    )

                    print(
                        "========================================"
                    )

                    # -------------------------------------------------
                    # CLAIM TASK
                    # -------------------------------------------------

                    task = await claim_task(
                        client,
                        task_id,
                    )

                    if task is None:
                        continue

                    # -------------------------------------------------
                    # GET PLAN
                    # -------------------------------------------------

                    plan = task.get("execution_plan") or task.get("plan", {})
                    approved_permission = task.get("approved_permission")

                    lease_id = task.get("lease_id")
                    if not lease_id:
                        print("[Mac Agent] Claimed task has no lease; skipping it.")
                        continue

                    # -------------------------------------------------
                    # EXECUTE PLAN
                    # -------------------------------------------------

                    stop_heartbeat = asyncio.Event()
                    heartbeat = asyncio.create_task(
                        renew_lease(client, task_id, lease_id, stop_heartbeat)
                    )
                    try:
                        # Desktop actions are synchronous.  Running them off
                        # the event loop keeps lease renewal and result upload
                        # responsive during long-running actions.
                        execution = await asyncio.to_thread(
                            execute_plan,
                            plan,
                            approved_permission,
                        )
                    except Exception as exc:
                        execution = {
                            "success": False,
                            "status": "failed",
                            "results": [{
                                "success": False,
                                "status": "execution_failed",
                                "result": f"{type(exc).__name__}: {exc}",
                            }],
                        }
                    finally:
                        stop_heartbeat.set()
                        await heartbeat

                    # -------------------------------------------------
                    # BUILD RESULT
                    # -------------------------------------------------

                    if execution["success"]:

                        status = "completed"

                    elif execution.get("status") == "permission_required":

                        status = "permission_required"

                    else:

                        status = "failed"

                    result = {
                        "goal": task.get(
                            "goal"
                        ),
                        "status": status,
                        "steps": execution[
                            "results"
                        ],
                    }

                    # -------------------------------------------------
                    # REPORT TO CLOUD
                    # -------------------------------------------------

                    entry = enqueue_result(task_id, status, result, lease_id)
                    if await report_result(client, entry):
                        # Remove the durable entry only after the Cloud accepts
                        # it (or confirms the lease was intentionally stale).
                        entries = [
                            item for item in load_outbox()
                            if item.get("lease_id") != lease_id
                        ]
                        save_outbox(entries)

                    print()
                    print(
                        f"[Mac Agent] Task "
                        f"{task_id} finished: "
                        f"{status}"
                    )

            except httpx.HTTPError as e:

                registered = False

                print(
                    f"[Mac Agent] Cloud connection error: {type(e).__name__}"
                )

            except Exception as e:

                registered = False

                print(
                    f"[Mac Agent] Unexpected error: {type(e).__name__}"
                )

            # ---------------------------------------------------------
            # WAIT
            # ---------------------------------------------------------

            await asyncio.sleep(
                POLL_INTERVAL
            )


# ---------------------------------------------------------
# START AGENT
# ---------------------------------------------------------

if __name__ == "__main__":

    try:

        asyncio.run(
            get_tasks()
        )

    except KeyboardInterrupt:

        print()
        print(
            "[Mac Agent] Agent stopped."
        )
