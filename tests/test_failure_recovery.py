"""Focused recovery checks that do not touch the real task or learning stores."""

import asyncio
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from agent import mac_agent
from automation.autonomous_engine import Task
from automation.simple_intent_router import SimpleIntentRouter
from cloud import server


class _Planner:
    def plan(self, goal):
        return {"tasks": [{"action": "open_app", "parameters": {}}]}


class _RecoveryPlanner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def plan(self, goal, prefer_learned=True, recovery_context=None):
        self.calls.append((goal, prefer_learned, recovery_context))
        if self.error:
            raise self.error
        return self.result


class _BlockingPlanner:
    """A planner that holds a worker thread until the test releases it."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def plan(self, goal):
        self.started.set()
        self.release.wait(timeout=5)
        return {"tasks": [{"action": "open_app", "parameters": {}}]}


class _FailingPlanner:
    def plan(self, goal):
        raise RuntimeError("Gemini planning failed after fallback attempts")


class _CountingPlanner:
    def __init__(self):
        self.calls = []

    def validate_plan(self, tasks):
        return True, ""

    def plan(self, goal):
        self.calls.append(goal)
        return {"tasks": [{"action": "open_app", "parameters": {}}]}


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None


class _Client:
    async def post(self, url, json):
        return _Response()


class FailureRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_task_file = server.TASK_FILE
        self.original_tasks = server.tasks
        self.original_planner = server.planner
        self.original_learn = server.engine.learning.learn
        self.original_planning_ids = server.planning_task_ids
        self.original_planning_workers = server.planning_workers
        server.TASK_FILE = Path(self.temp_dir.name) / "tasks.json"
        server.tasks = {}
        server.planning_task_ids = set()
        server.planning_workers = {}
        server.engine.learning.learn = lambda **_kwargs: SimpleNamespace(
            name="test workflow", steps=[], success_count=1,
        )

    async def asyncTearDown(self):
        server.TASK_FILE = self.original_task_file
        server.tasks = self.original_tasks
        server.planner = self.original_planner
        server.engine.learning.learn = self.original_learn
        server.planning_task_ids = self.original_planning_ids
        server.planning_workers = self.original_planning_workers
        self.temp_dir.cleanup()

    @staticmethod
    def blocked_result(reason="Action 'computer_control' is not allowed automatically."):
        return {
            "steps": [
                {"success": True, "action": "open_app", "result": "Safari opened"},
                {
                    "success": False,
                    "action": "computer_control",
                    "status": "blocked",
                    "result": reason,
                },
            ]
        }

    @staticmethod
    def running_task(recovery_attempts=0):
        return {
            "id": "task", "goal": "Open Safari and adjust a system setting",
            "device": "mac", "status": "running", "claimed_by": "mac",
            "lease_id": "old-lease", "lease_expires_at": datetime.now().isoformat(),
            "plan": {"tasks": [
                {"action": "open_app", "parameters": {"app": "Safari"}},
                {"action": "computer_control", "parameters": {"action": "click"}},
            ]},
            "blocked_recovery_attempt_count": recovery_attempts,
        }

    async def report_blocked_result(self, result=None):
        return await server.update_task_result(
            "task",
            server.TaskResultRequest(
                status="failed", result=result or self.blocked_result(),
                device="mac", lease_id="old-lease",
            ),
        )

    async def test_blocked_action_safe_alternative_requeues_with_full_context(self):
        planner = _RecoveryPlanner({
            "tasks": [{"action": "browser_control", "parameters": {"action": "navigate"}}]
        })
        server.planner = planner
        server.tasks = {"task": self.running_task()}

        task = await self.report_blocked_result()

        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["plan"], planner.result)
        self.assertEqual(task["blocked_recovery_attempt_count"], 1)
        self.assertEqual(len(task["blocked_recovery_history"]), 1)
        _, prefer_learned, context = planner.calls[0]
        self.assertFalse(prefer_learned)
        self.assertEqual(context["original_goal"], task["goal"])
        self.assertEqual(context["completed_steps"][0]["parameters"], {"app": "Safari"})
        self.assertEqual(context["failed_step"], {
            "action": "computer_control",
            "parameters": {"action": "click"},
            "description": "",
            "status": "blocked",
            "failure_reason": "Action 'computer_control' is not allowed automatically.",
        })

    async def test_blocked_action_context_supports_planner_task_objects(self):
        planner = _RecoveryPlanner({"tasks": [{"action": "open_app", "parameters": {}}]})
        server.planner = planner
        task = self.running_task()
        task["plan"] = {"tasks": [
            Task("open_app", {"app": "Safari"}, "Open the browser"),
            Task("computer_control", {"action": "click"}, "Change the setting"),
        ]}
        server.tasks = {"task": task}

        recovered = await self.report_blocked_result()

        self.assertEqual(recovered["status"], "queued")
        context = planner.calls[0][2]
        self.assertEqual(context["completed_steps"][0], {
            "action": "open_app",
            "parameters": {"app": "Safari"},
            "description": "Open the browser",
            "result": "Safari opened",
        })
        self.assertEqual(context["failed_step"], {
            "action": "computer_control",
            "parameters": {"action": "click"},
            "description": "Change the setting",
            "status": "blocked",
            "failure_reason": "Action 'computer_control' is not allowed automatically.",
        })

    async def test_blocked_action_without_safe_alternative_needs_review(self):
        server.planner = _RecoveryPlanner({
            "tasks": [{"action": "computer_control", "parameters": {"action": "click"}}]
        })
        server.tasks = {"task": self.running_task()}

        task = await self.report_blocked_result()

        self.assertEqual(task["status"], "needs_review")
        self.assertIn("Mac-safe alternative", task["error"])
        self.assertEqual(task["result"], self.blocked_result())

    async def test_blocked_recovery_attempt_limit_needs_review(self):
        planner = _RecoveryPlanner({"tasks": [{"action": "open_app", "parameters": {}}]})
        server.planner = planner
        server.tasks = {"task": self.running_task(server.MAX_BLOCKED_ACTION_RECOVERY_REPLANS)}

        task = await self.report_blocked_result()

        self.assertEqual(task["status"], "needs_review")
        self.assertIn("attempt limit", task["error"])
        self.assertEqual(planner.calls, [])

    async def test_blocked_recovery_planner_failure_needs_review(self):
        server.planner = _RecoveryPlanner(error=RuntimeError("planner unavailable"))
        server.tasks = {"task": self.running_task()}

        task = await self.report_blocked_result()

        self.assertEqual(task["status"], "needs_review")
        self.assertEqual(task["error"], "planner unavailable")

    async def test_stale_blocked_recovery_result_cannot_overwrite_reclaimed_task(self):
        server.planner = _RecoveryPlanner({"tasks": [{"action": "open_app", "parameters": {}}]})
        server.tasks = {"task": self.running_task()}
        await self.report_blocked_result()
        claimed = await server.claim_task("task", server.TaskClaimRequest(device="mac"))

        replay = await self.report_blocked_result()

        self.assertEqual(replay["status"], "running")
        self.assertEqual(server.tasks["task"]["status"], "running")
        self.assertEqual(server.tasks["task"]["lease_id"], claimed["lease_id"])

    async def test_expired_safe_lease_requeues_and_unsafe_lease_needs_review(self):
        expired = (datetime.now() - timedelta(seconds=1)).isoformat()
        server.tasks = {
            "safe": {
                "id": "safe", "device": "mac", "status": "running",
                "plan": {"tasks": [{"action": "open_app", "parameters": {}}]},
                "attempt_count": 1, "lease_expires_at": expired,
            },
            "unsafe": {
                "id": "unsafe", "device": "mac", "status": "running",
                "plan": {"tasks": [{"action": "computer_control", "parameters": {}}]},
                "attempt_count": 1, "lease_expires_at": expired,
            },
        }
        self.assertEqual(server.recover_expired_leases(), 2)
        self.assertEqual(server.tasks["safe"]["status"], "queued")
        self.assertEqual(server.tasks["unsafe"]["status"], "needs_review")

    async def test_lease_result_is_idempotent_and_stalled_planning_recovers(self):
        server.tasks = {
            "task": {
                "id": "task", "goal": "Open Safari", "device": "mac", "status": "queued",
                "plan": {"tasks": [{"action": "open_app", "parameters": {}}]},
                "attempt_count": 0,
            }
        }
        claimed = await server.claim_task("task", server.TaskClaimRequest(device="mac"))
        result = server.TaskResultRequest(
            status="completed", result={"steps": [{"success": True}]},
            device="mac", lease_id=claimed["lease_id"],
        )
        await server.update_task_result("task", result)
        self.assertEqual((await server.update_task_result("task", result))["status"], "completed")

        server.planner = _Planner()
        server.tasks = {
            "planning": {
                "id": "planning", "goal": "Perform a complex task", "device": "mac", "status": "planning",
                "created_at": (datetime.now() - timedelta(minutes=10)).isoformat(),
                "planning_attempt_count": 0,
            }
        }
        self.assertEqual(await server.recover_stalled_planning(), 1)
        self.assertEqual(server.tasks["planning"]["status"], "queued")

    async def test_new_planning_does_not_block_device_task_polling(self):
        planner = _BlockingPlanner()
        server.planner = planner

        created = await server.create_task(
            server.TaskRequest(goal="Perform a complex task", device="mac")
        )
        self.assertEqual(created["status"], "planning")

        # Let the background task enter its synchronous planner call, then
        # verify an agent queue poll still gets an immediate response.  Mark
        # it stale and scan recovery too: the active-worker guard must prevent
        # a duplicate planning job.
        await asyncio.wait_for(asyncio.to_thread(planner.started.wait, 1), timeout=2)
        created["planning_started_at"] = (
            datetime.now() - timedelta(minutes=10)
        ).isoformat()
        self.assertFalse(server.start_task_planning(created["id"]))
        self.assertEqual(await server.recover_stalled_planning(), 0)

        polled = await asyncio.wait_for(
            server.get_device_tasks("mac-main"), timeout=0.2,
        )
        self.assertEqual(polled, [])

        planner.release.set()
        for _ in range(20):
            if created["id"] not in server.planning_task_ids:
                break
            await asyncio.sleep(0.01)

        self.assertNotIn(created["id"], server.planning_task_ids)
        self.assertEqual(server.tasks[created["id"]]["status"], "queued")
        self.assertEqual(server.tasks[created["id"]]["device"], "mac-main")
        self.assertEqual(
            (await server.get_device_tasks("mac-main"))[0]["id"],
            created["id"],
        )

    async def test_simple_open_app_intent_skips_gemini_and_queues_valid_plan(self):
        planner = _CountingPlanner()
        server.planner = planner

        created = await server.create_task(
            server.TaskRequest(goal="Open Safari", device="mac")
        )
        for _ in range(20):
            if created["id"] not in server.planning_task_ids:
                break
            await asyncio.sleep(0.01)

        planned = server.tasks[created["id"]]
        self.assertEqual(planner.calls, [])
        self.assertEqual(planned["status"], "queued")
        self.assertEqual(planned["plan"]["source"], "simple_intent")
        self.assertEqual(planned["plan"]["tasks"], [{
            "action": "open_app",
            "parameters": {"app_name": "Safari"},
            "description": "Open Safari",
        }])

    async def test_ambiguous_and_unsafe_intents_fall_back_or_do_not_route(self):
        router = SimpleIntentRouter()
        for app_name in ("Safari", "Chrome", "Calculator", "Finder"):
            plan = router.route(f"Open {app_name}")
            self.assertIsNotNone(plan)
            self.assertEqual(plan.tasks[0].action, "open_app")
            self.assertEqual(plan.tasks[0].parameters, {"app_name": app_name})

        self.assertIsNone(router.route("Open something"))
        self.assertIsNone(router.route("Click the button in Safari"))

    async def test_planner_failure_becomes_a_persisted_terminal_task(self):
        server.planner = _FailingPlanner()

        created = await server.create_task(
            server.TaskRequest(goal="Perform a complex task", device="mac")
        )
        self.assertEqual(created["status"], "planning")

        for _ in range(20):
            if created["id"] not in server.planning_task_ids:
                break
            await asyncio.sleep(0.01)

        failed = server.tasks[created["id"]]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("fallback attempts", failed["error"])
        self.assertIsNone(failed["plan"])
        self.assertIsNone(failed["planned_at"])

    async def test_agent_outbox_retries_acknowledged_result(self):
        old_outbox = mac_agent.OUTBOX_FILE
        mac_agent.OUTBOX_FILE = Path(self.temp_dir.name) / "outbox.json"
        try:
            entry = mac_agent.enqueue_result("task", "completed", {"steps": []}, "lease")
            self.assertTrue(await mac_agent.report_result(_Client(), entry))
            await mac_agent.flush_outbox(_Client())
            self.assertEqual(mac_agent.load_outbox(), [])
        finally:
            mac_agent.OUTBOX_FILE = old_outbox

    async def test_parse_time_accepts_iso_timestamps_and_normalizes_timezone(self):
        parsed = server.parse_time("2026-09-18T12:34:56Z")
        self.assertIsNotNone(parsed)
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed, datetime(2026, 9, 18, 12, 34, 56))
        self.assertIsNotNone(server.parse_time("2026-09-18T12:34:56+00:00"))
        self.assertIsNone(server.parse_time("not-a-timestamp"))

    async def test_permission_required_result_creates_pending_request_without_execution(self):
        server.tasks = {
            "permission-task": {
                "id": "permission-task", "goal": "Click a button", "device": "mac",
                "status": "queued", "plan": {"tasks": [
                    {"action": "browser_control", "parameters": {"action": "click", "selector": "Buy"}},
                ]}, "attempt_count": 0,
            }
        }
        claimed = await server.claim_task(
            "permission-task", server.TaskClaimRequest(device="mac")
        )
        lease_id = claimed["lease_id"]
        result = {
            "steps": [{
                "success": False, "action": "browser_control",
                "parameters": {"action": "click", "selector": "Buy"},
                "step_index": 0, "status": "permission_required",
                "result": "Action is not allowed automatically.",
            }]
        }

        task = await server.update_task_result(
            "permission-task",
            server.TaskResultRequest(
                status="permission_required", result=result,
                device="mac", lease_id=lease_id,
            ),
        )

        self.assertEqual(task["status"], "waiting_permission")
        self.assertEqual(task["permission_request"]["status"], "pending")
        self.assertEqual(task["permission_request"]["action"], "browser_control")
        self.assertIsNone(task["lease_id"])
        replay = await server.update_task_result(
            "permission-task",
            server.TaskResultRequest(
                status="permission_required", result=result,
                device="mac", lease_id=lease_id,
            ),
        )
        self.assertEqual(replay["permission_request"]["requested_lease_id"], lease_id)

    async def test_permission_approval_continues_exact_step_only(self):
        server.tasks = {
            "permission-task": {
                "id": "permission-task", "goal": "Open Safari and click", "device": "mac",
                "status": "queued", "plan": {"tasks": [
                    {"action": "open_app", "parameters": {"app_name": "Safari"}},
                    {"action": "browser_control", "parameters": {"action": "click", "selector": "Buy"}},
                ]}, "attempt_count": 0,
            }
        }
        claimed = await server.claim_task(
            "permission-task", server.TaskClaimRequest(device="mac")
        )
        pending = {
            "steps": [
                {"success": True, "action": "open_app", "parameters": {"app_name": "Safari"}, "step_index": 0},
                {"success": False, "action": "browser_control", "parameters": {"action": "click", "selector": "Buy"}, "step_index": 1, "status": "permission_required"},
            ]
        }
        await server.update_task_result(
            "permission-task",
            server.TaskResultRequest(status="permission_required", result=pending, device="mac", lease_id=claimed["lease_id"]),
        )

        approved = await server.approve_task_permission(
            "permission-task",
            server.PermissionApprovalRequest(
                action="browser_control",
                parameters={"action": "click", "selector": "Buy"},
            ),
        )
        self.assertEqual(approved["status"], "queued")
        self.assertEqual(approved["execution_plan"]["tasks"], [
            {"action": "browser_control", "parameters": {"action": "click", "selector": "Buy"}},
        ])
        self.assertEqual(approved["approved_permission"]["step_index"], 0)

        resumed = await server.claim_task(
            "permission-task", server.TaskClaimRequest(device="mac")
        )
        completed = await server.update_task_result(
            "permission-task",
            server.TaskResultRequest(
                status="completed",
                result={"steps": [{"success": True, "action": "browser_control", "parameters": {"action": "click", "selector": "Buy"}}]},
                device="mac", lease_id=resumed["lease_id"],
            ),
        )
        self.assertEqual(completed["status"], "completed")

    async def test_permission_denial_is_not_executable(self):
        task = {
            "id": "permission-task", "goal": "Click", "device": "mac", "status": "waiting_permission",
            "permission_request": {"status": "pending", "action": "browser_control", "parameters": {"action": "click"}},
        }
        server.tasks = {"permission-task": task}
        denied = await server.deny_task_permission("permission-task")
        self.assertEqual(denied["status"], "needs_review")
        self.assertEqual(denied["permission_request"]["status"], "denied")
        self.assertIsNone(denied["approved_permission"] if "approved_permission" in denied else None)

        with self.assertRaises(Exception):
            await server.approve_task_permission(
                "permission-task",
                server.PermissionApprovalRequest(
                    action="browser_control",
                    parameters={"action": "click"},
                ),
            )

    async def test_permission_approval_is_task_scoped_and_completed_tasks_do_not_reopen(self):
        request = {
            "action": "browser_control",
            "parameters": {"action": "click", "selector": "Buy"},
        }
        server.tasks = {
            "task-a": {
                "id": "task-a", "device": "mac", "status": "waiting_permission",
                "plan": {"tasks": [request]},
                "permission_request": {
                    "task_id": "task-a", **request, "step_index": 0,
                    "status": "pending", "requested_by": "mac", "requested_lease_id": "lease-a",
                },
            },
            "task-b": {
                "id": "task-b", "device": "mac", "status": "waiting_permission",
                "plan": {"tasks": [{"action": "browser_control", "parameters": {"action": "click", "selector": "Other"}}]},
                "permission_request": {
                    "task_id": "task-b", "action": "browser_control",
                    "parameters": {"action": "click", "selector": "Other"}, "step_index": 0,
                    "status": "pending", "requested_by": "mac", "requested_lease_id": "lease-b",
                },
            },
        }

        with self.assertRaises(Exception):
            await server.approve_task_permission("task-b", server.PermissionApprovalRequest(**request))

        approved = await server.approve_task_permission(
            "task-a", server.PermissionApprovalRequest(**request)
        )
        self.assertEqual(approved["status"], "queued")
        self.assertEqual(approved["execution_plan"]["tasks"], [request])

        completed = {
            "id": "completed-task", "device": "mac", "status": "completed",
            "permission_request": {"status": "approved", **request},
        }
        server.tasks = {"completed-task": completed}
        result = await server.approve_task_permission(
            "completed-task", server.PermissionApprovalRequest(**request)
        )
        self.assertIs(result, completed)
        self.assertEqual(result["status"], "completed")

    async def test_waiting_permission_is_not_recovered_by_lease_scan(self):
        server.tasks = {
            "permission-task": {
                "id": "permission-task", "device": "mac", "status": "waiting_permission",
                "lease_id": None, "lease_expires_at": None,
                "permission_request": {"status": "pending"},
            }
        }
        self.assertEqual(server.recover_expired_leases(), 0)
        self.assertEqual(server.tasks["permission-task"]["status"], "waiting_permission")

    async def test_approved_permission_is_exact_and_stale_unsafe_lease_is_not_replayed(self):
        approved = {"action": "browser_control", "parameters": {"action": "click", "selector": "Buy"}}
        self.assertFalse(mac_agent.is_action_allowed("browser_control", approved["parameters"]))
        self.assertFalse(mac_agent.is_action_allowed("browser_control", {"action": "click", "selector": "Other"}))

        server.tasks = {
            "approved-task": {
                "id": "approved-task", "device": "mac", "status": "running",
                "lease_id": "stale", "lease_expires_at": (datetime.now() - timedelta(seconds=1)).isoformat(),
                "attempt_count": 1, "plan": {"tasks": [approved]},
            }
        }
        self.assertEqual(server.recover_expired_leases(), 1)
        self.assertEqual(server.tasks["approved-task"]["status"], "needs_review")

    async def test_agent_executes_only_matching_approved_action(self):
        class Registry:
            def has(self, action):
                return action == "computer_control"

            def run(self, action, parameters):
                return "clicked"

        old_registry = mac_agent.action_registry
        mac_agent.action_registry = Registry()
        try:
            execution = mac_agent.execute_plan(
                {"tasks": [{"action": "computer_control", "parameters": {"action": "click"}}]},
                {"action": "computer_control", "parameters": {"action": "click"}, "step_index": 0},
            )
            self.assertTrue(execution["success"])
            blocked = mac_agent.execute_plan(
                {"tasks": [{"action": "computer_control", "parameters": {"action": "fill"}}]},
                {"action": "computer_control", "parameters": {"action": "click"}, "step_index": 0},
            )
            self.assertEqual(blocked["status"], "permission_required")
        finally:
            mac_agent.action_registry = old_registry

    async def test_agent_does_not_report_error_string_as_success(self):
        class Registry:
            def has(self, action):
                return action == "browser_control"

            def run(self, action, parameters):
                return "Could not find element: 'Buy button'"

        old_registry = mac_agent.action_registry
        mac_agent.action_registry = Registry()
        try:
            result = mac_agent.execute_action(
                "browser_control",
                {"action": "smart_click", "description": "Buy button"},
                {"action": "browser_control", "parameters": {"action": "smart_click", "description": "Buy button"}},
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "execution_failed")
        finally:
            mac_agent.action_registry = old_registry

    async def test_deterministic_permission_approval_executes_exact_step_once(self):
        parameters = {"action": "smart_click", "description": "Test button"}
        plan = {"tasks": [{
            "action": "browser_control",
            "parameters": parameters,
            "description": "Click the test button",
        }]}
        server.tasks = {
            "deterministic-approval": {
                "id": "deterministic-approval",
                "goal": "Click the test button",
                "device": "mac-main",
                "status": "waiting_permission",
                "plan": plan,
                "permission_request": {
                    "task_id": "deterministic-approval",
                    "action": "browser_control",
                    "parameters": parameters,
                    "description": "Click the test button",
                    "step_index": 0,
                    "status": "pending",
                    "requested_by": "mac-main",
                    "requested_lease_id": "permission-lease",
                },
                "lease_id": None,
                "lease_expires_at": None,
                "attempt_count": 0,
            }
        }

        approved = await server.approve_task_permission(
            "deterministic-approval",
            server.PermissionApprovalRequest(
                action="browser_control",
                parameters=parameters,
            ),
        )
        self.assertEqual(approved["status"], "queued")
        self.assertEqual(approved["execution_plan"]["tasks"], plan["tasks"])

        class Registry:
            def __init__(self):
                self.calls = []

            def has(self, action):
                return action == "browser_control"

            def run(self, action, values):
                self.calls.append((action, dict(values)))
                return "Clicked: 'Test button'"

        old_registry = mac_agent.action_registry
        registry = Registry()
        mac_agent.action_registry = registry
        try:
            claimed = await server.claim_task(
                "deterministic-approval",
                server.TaskClaimRequest(device="mac"),
            )
            execution = mac_agent.execute_plan(
                claimed["execution_plan"],
                claimed["approved_permission"],
            )
            self.assertTrue(execution["success"])
            self.assertEqual(registry.calls, [("browser_control", parameters)])

            completed = await server.update_task_result(
                "deterministic-approval",
                server.TaskResultRequest(
                    status="completed",
                    result={"steps": execution["results"]},
                    device="mac",
                    lease_id=claimed["lease_id"],
                ),
            )
            self.assertEqual(completed["status"], "completed")
            duplicate_approval = await server.approve_task_permission(
                "deterministic-approval",
                server.PermissionApprovalRequest(
                    action="browser_control",
                    parameters=parameters,
                ),
            )
            self.assertEqual(duplicate_approval["status"], "completed")
            self.assertEqual(len(registry.calls), 1)
        finally:
            mac_agent.action_registry = old_registry

    async def test_deterministic_permission_denial_cannot_be_approved_later(self):
        parameters = {"action": "smart_click", "description": "Denied button"}
        server.tasks = {
            "deterministic-denial": {
                "id": "deterministic-denial", "device": "mac-main",
                "status": "waiting_permission",
                "plan": {"tasks": [{"action": "browser_control", "parameters": parameters}]},
                "permission_request": {
                    "task_id": "deterministic-denial", "action": "browser_control",
                    "parameters": parameters, "step_index": 0, "status": "pending",
                },
            }
        }
        denied = await server.deny_task_permission("deterministic-denial")
        self.assertEqual(denied["status"], "needs_review")
        with self.assertRaises(Exception):
            await server.approve_task_permission(
                "deterministic-denial",
                server.PermissionApprovalRequest(
                    action="browser_control", parameters=parameters,
                ),
            )

    async def test_agent_blocks_unsafe_action_and_allows_safe_operations(self):
        self.assertTrue(mac_agent.is_action_allowed("open_app", {"app_name": "Safari"}))
        self.assertTrue(mac_agent.is_action_allowed("browser_control", {"action": "navigate", "url": "https://example.com"}))
        self.assertFalse(mac_agent.is_action_allowed("browser_control", {"action": "click"}))
        execution = mac_agent.execute_plan({"tasks": [
            {"action": "browser_control", "parameters": {"action": "click"}},
        ]})
        self.assertEqual(execution["status"], "permission_required")
        self.assertEqual(execution["results"][0]["status"], "permission_required")

    async def test_device_alias_and_heartbeat_remain_compatible(self):
        server.tasks = {
            "task": {"id": "task", "device": "mac", "status": "queued", "plan": {"tasks": []}, "attempt_count": 0}
        }
        claimed = await server.claim_task("task", server.TaskClaimRequest(device="mac"))
        self.assertEqual(claimed["device"], "mac-main")
        heartbeat = await server.heartbeat_task(
            "task",
            server.TaskHeartbeatRequest(device="mac", lease_id=claimed["lease_id"]),
        )
        self.assertEqual(heartbeat["task_id"], "task")


if __name__ == "__main__":
    unittest.main()
