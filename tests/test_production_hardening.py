"""Small contracts for the Cloud/Agent production boundary."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import mac_agent
from automation.permission_manager import PermissionManager
from cloud import auth, server
from cloud.activity_log import ActivityLog
from cloud.settings import CloudSettings


def test_production_settings_require_secrets_without_revealing_values(tmp_path):
    config = CloudSettings(
        environment="production",
        host="0.0.0.0",
        port=8000,
        gemini_api_key=None,
        admin_token=None,
        agent_token=None,
        cors_origins=(),
        data_dir=tmp_path,
        task_file=tmp_path / "tasks.json",
        devices_file=tmp_path / "devices.json",
        activity_file=tmp_path / "activity.json",
        uploads_dir=tmp_path / "uploads",
    )
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        config.validate()


def test_admin_and_agent_routes_are_separated():
    original = auth.settings
    auth.settings = replace(
        original,
        environment="production",
        gemini_api_key="configured",
        admin_token="admin-test-token",
        agent_token="agent-test-token",
    )
    try:
        with TestClient(server.app) as client:
            assert client.get("/tasks").status_code == 401
            assert client.get(
                "/tasks", headers={"Authorization": "Bearer admin-test-token"}
            ).status_code == 200
            assert client.get(
                "/devices/mac-main/tasks",
                headers={
                    "Authorization": "Bearer agent-test-token",
                    "X-Jarvis-Device-ID": "mac-main",
                },
            ).status_code == 200
            assert client.get(
                "/devices", headers={"Authorization": "Bearer agent-test-token"}
            ).status_code == 401
    finally:
        auth.settings = original


def test_engineering_routes_use_admin_auth_and_report_action_count():
    original = auth.settings
    auth.settings = replace(
        original,
        environment="production",
        gemini_api_key="configured",
        admin_token="admin-test-token",
        agent_token="agent-test-token",
    )
    try:
        with TestClient(server.app) as client:
            assert client.get("/engineering/tasks").status_code == 401
            diagnostics = client.get(
                "/engineering/diagnostics",
                headers={"Authorization": "Bearer admin-test-token"},
            )
            assert diagnostics.status_code == 200
            payload = diagnostics.json()
            assert payload["active_action_count"] == 18
            assert payload["version"]
    finally:
        auth.settings = original


def test_activity_log_survives_restart(tmp_path):
    path = tmp_path / "activity.json"
    first = ActivityLog(path=path)
    first.publish("TASK", "persisted")

    second = ActivityLog(path=path)
    assert second.cursor() == 1
    assert second.latest()[0]["message"] == "persisted"


def test_task_store_survives_reload(tmp_path):
    original_path = server.TASK_FILE
    original_tasks = server.tasks
    server.TASK_FILE = tmp_path / "tasks.json"
    server.tasks = {"task-1": {"id": "task-1", "status": "queued"}}
    try:
        server.save_tasks()
        server.tasks = {}
        server.load_tasks()
        assert server.tasks["task-1"]["status"] == "queued"
    finally:
        server.TASK_FILE = original_path
        server.tasks = original_tasks


def test_permission_store_survives_restart(tmp_path):
    path = tmp_path / "permissions.json"
    first = PermissionManager(path=path)
    first.set_permission("browser_control", "safe")

    second = PermissionManager(path=path)
    assert second.permissions["browser_control"] == "safe"


def test_agent_uses_authenticated_outbound_headers(monkeypatch):
    monkeypatch.setattr(mac_agent, "AGENT_TOKEN", "agent-test-token")
    headers = mac_agent.request_headers()
    assert headers["Authorization"] == "Bearer agent-test-token"
    assert headers["X-Jarvis-Device-ID"] == mac_agent.DEVICE_NAME


def test_production_paths_can_be_selected_by_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from core.runtime_config import runtime_path

    assert runtime_path("tasks.json", Path("legacy/tasks.json")) == tmp_path / "tasks.json"
