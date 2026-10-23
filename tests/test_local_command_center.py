from __future__ import annotations

from fastapi.testclient import TestClient

from automation.autonomous_runtime import AutonomousRuntime
from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from dashboard.local_command_center import HOST, create_app, is_loopback_peer


def local_client(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 8765),
    )


class FakeRegistry:
    def __init__(self, *, fail_retry: bool = False):
        self.calls = []
        self.fail_retry = fail_retry

    def names(self):
        return {
            "weather_report",
            "generate_briefing",
            "send_message",
            "retry_action",
            "file_controller",
        }

    def has(self, action: str) -> bool:
        return action in self.names()

    def run(self, name: str, parameters=None, ctx=None):
        self.calls.append(name)
        if name == "weather_report":
            return "Local test weather"
        if name == "generate_briefing":
            return "Harmless test briefing"
        if name == "send_message":
            return "Test message sent"
        if name == "file_controller":
            return "Local test context"
        if name == "retry_action":
            if self.fail_retry or int((parameters or {}).get("attempt", 0)) >= 99:
                raise RuntimeError("deterministic test failure")
            return "retry recovered"
        raise AssertionError(f"Unexpected action dispatched: {name}")


def make_runtime(tmp_path, registry: FakeRegistry | None = None) -> AutonomousRuntime:
    permissions = PermissionManager(path=tmp_path / "permissions.json")
    permissions.allow_automatically("file_controller")
    return AutonomousRuntime(
        action_registry=registry or FakeRegistry(),
        permissions=permissions,
        learning_memory=LearningMemory(path=tmp_path / "learning-memory.json"),
    )


def test_status_uses_runtime_and_exposes_available_local_summaries(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.status_store["summary"] = {
        "accounts": [{"status": "UNAVAILABLE", "cookie": "session-private"}]
    }
    briefings = [{"definition_id": "morning-briefing", "status": "delivered", "detail": "Local record"}]

    with local_client(create_app(
        runtime=runtime,
        briefing_reader=lambda: briefings,
    )) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    status = response.json()
    assert status["ready"] is True
    assert status["local_only"] is True
    assert status["host"] == "127.0.0.1"
    assert status["runtime_state"] == "IDLE"
    assert status["registered_actions"] == 5
    assert status["briefings"] == briefings
    assert "calls" in status
    assert status["social_summary"] == {
        "accounts": [{"status": "UNAVAILABLE", "cookie": "[REDACTED]"}]
    }


def test_goal_submission_uses_autonomous_runtime_and_reports_outcome(tmp_path):
    registry = FakeRegistry()
    runtime = make_runtime(tmp_path, registry)
    app = create_app(runtime=runtime, briefing_reader=list)

    with local_client(app) as client:
        submitted = client.post(
            "/api/goals",
            headers={"Idempotency-Key": "harmless-goal-1"},
            json={"goal": "Prepare my morning briefing"},
        )
        task_id = submitted.json()["task"]["task_id"]
        completed = app.state.command_center.wait_for_task(task_id)
        task = client.get("/api/tasks").json()["tasks"][0]
        task_by_id = client.get(f"/api/tasks/{task_id}").json()["task"]
        events = client.get("/api/activity").json()["events"]

    assert submitted.status_code == 202
    assert submitted.json()["duplicate"] is False
    assert completed["status"] == "COMPLETED"
    assert task["status"] == "COMPLETED"
    assert task_by_id["task_id"] == task_id
    assert task_by_id["status"] == "COMPLETED"
    assert task["progress"]["completed"] == task["progress"]["total"] == 2
    assert registry.calls == ["weather_report", "generate_briefing"]
    assert [event["category"] for event in events][-2:] == ["TASK", "TASK"]


def test_pending_permission_is_displayed_and_action_waits_for_runtime_approval(tmp_path):
    registry = FakeRegistry()
    runtime = make_runtime(tmp_path, registry)
    app = create_app(runtime=runtime, briefing_reader=list)

    with local_client(app) as client:
        submitted = client.post(
            "/api/goals",
            json={"goal": "Prepare and send my morning summary"},
        )
        task_id = submitted.json()["task"]["task_id"]
        waiting = app.state.command_center.wait_for_task(task_id)
        shown = client.get("/api/tasks").json()["tasks"][0]

        assert waiting["status"] == "AWAITING_PERMISSION"
        assert shown["approvals"][0]["action"] == "send_message"
        assert "send_message" not in registry.calls

        approval = client.post(
            f"/api/tasks/{task_id}/approval",
            json={"approved": True},
        )
        finished = app.state.command_center.wait_for_task(task_id)

    assert approval.status_code == 200
    assert finished["status"] == "COMPLETED"
    assert registry.calls.count("send_message") == 1


def test_denied_permission_never_executes_protected_action(tmp_path):
    registry = FakeRegistry()
    runtime = make_runtime(tmp_path, registry)
    app = create_app(runtime=runtime, briefing_reader=list)

    with local_client(app) as client:
        task_id = client.post(
            "/api/goals",
            json={"goal": "Prepare and send my morning summary"},
        ).json()["task"]["task_id"]
        app.state.command_center.wait_for_task(task_id)
        response = client.post(
            f"/api/tasks/{task_id}/approval",
            json={"approved": False},
        )
        stopped = app.state.command_center.wait_for_task(task_id)

    assert response.status_code == 200
    assert stopped["status"] == "BLOCKED"
    assert "send_message" not in registry.calls


def test_failed_runtime_task_is_visible_as_failed(tmp_path):
    runtime = make_runtime(tmp_path, FakeRegistry(fail_retry=True))
    app = create_app(runtime=runtime, briefing_reader=list)

    with local_client(app) as client:
        response = client.post("/api/goals", json={"goal": "Permanent failure path"})
        task_id = response.json()["task"]["task_id"]
        task = app.state.command_center.wait_for_task(task_id)

    assert response.status_code == 202
    assert task["status"] == "FAILED"
    assert task["outcome"]["status"] == "STOP_SAFELY"


def test_duplicate_submission_is_idempotent_and_does_not_rerun(tmp_path):
    registry = FakeRegistry()
    app = create_app(runtime=make_runtime(tmp_path, registry), briefing_reader=list)

    with local_client(app) as client:
        payload = {"goal": "Prepare my morning briefing"}
        headers = {"Idempotency-Key": "repeat-safe-request"}
        first = client.post("/api/goals", headers=headers, json=payload)
        task_id = first.json()["task"]["task_id"]
        app.state.command_center.wait_for_task(task_id)
        repeated = client.post("/api/goals", headers=headers, json=payload)

    assert repeated.status_code == 202
    assert repeated.json()["duplicate"] is True
    assert repeated.json()["task"]["task_id"] == task_id
    assert registry.calls == ["weather_report", "generate_briefing"]


def test_local_interface_rejects_non_loopback_peers():
    assert HOST == "127.0.0.1"
    assert is_loopback_peer("127.0.0.1") is True
    assert is_loopback_peer("::1") is True
    assert is_loopback_peer("192.0.2.10") is False
    assert is_loopback_peer("localhost") is False
    assert is_loopback_peer(None) is False


def test_http_middleware_rejects_non_loopback_peer(tmp_path):
    app = create_app(runtime=make_runtime(tmp_path), briefing_reader=list)

    with TestClient(app, client=("192.0.2.10", 54000)) as client:
        response = client.get("/api/status")

    assert response.status_code == 403
    assert "loopback" in response.json()["error"]


def test_http_middleware_rejects_dns_rebinding_host(tmp_path):
    app = create_app(runtime=make_runtime(tmp_path), briefing_reader=list)

    with TestClient(
        app,
        base_url="http://attacker.invalid:8765",
        client=("127.0.0.1", 54000),
    ) as client:
        response = client.get("/api/status")

    assert response.status_code == 403
    assert "configured loopback host" in response.json()["error"]


def test_cross_origin_command_submission_is_rejected(tmp_path):
    registry = FakeRegistry()
    app = create_app(runtime=make_runtime(tmp_path, registry), briefing_reader=list)

    with local_client(app) as client:
        response = client.post(
            "/api/goals",
            headers={"Origin": "https://attacker.invalid"},
            json={"goal": "Prepare my morning briefing"},
        )

    assert response.status_code == 403
    assert registry.calls == []


def test_invalid_goal_is_rejected_without_echoing_the_request_body(tmp_path):
    app = create_app(runtime=make_runtime(tmp_path), briefing_reader=list)
    too_long_goal = "api_key=must-not-echo " + "x" * 2000

    with local_client(app) as client:
        response = client.post("/api/goals", json={"goal": too_long_goal})

    assert response.status_code == 422
    assert "must-not-echo" not in response.text
    assert "character" in response.text


def test_runtime_unavailable_is_reported_without_fabricating_readiness():
    def unavailable():
        raise RuntimeError("local runtime cannot be initialized")

    with local_client(create_app(runtime_factory=unavailable, briefing_reader=list)) as client:
        status = client.get("/api/status")
        submit = client.post("/api/goals", json={"goal": "Check local status"})

    assert status.status_code == 200
    assert status.json()["ready"] is False
    assert "cannot be initialized" in status.json()["error"]
    assert submit.status_code == 503


def test_configured_external_data_directory_prevents_runtime_start(monkeypatch):
    from dashboard.local_command_center import build_local_runtime

    monkeypatch.setenv("JARVIS_DATA_DIR", "/configured/external/path")

    try:
        build_local_runtime()
    except RuntimeError as exc:
        assert "Unset it" in str(exc)
    else:
        raise AssertionError("The local runtime accepted a configured external data directory.")


def test_goal_with_secret_material_is_rejected_without_persisting_it(tmp_path):
    registry = FakeRegistry()
    app = create_app(runtime=make_runtime(tmp_path, registry), briefing_reader=list)

    with local_client(app) as client:
        response = client.post(
            "/api/goals",
            json={
                "goal": (
                    "Use api_key=api-private "
                    "Authorization: Bearer bearer-private "
                    "password=my private phrase"
                )
            },
        )

    assert response.status_code == 422
    for secret in ("api-private", "bearer-private", "my private phrase"):
        assert secret not in response.text
    assert "credentials or authentication data" in response.json()["detail"]
    assert registry.calls == []
