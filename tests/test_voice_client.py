import asyncio

from dashboard.voice_client import (
    VoiceCommandCenterClient,
    voice_result_text,
)


def run(coro):
    return asyncio.run(coro)


def test_voice_goal_uses_command_center_and_waits_for_completion():
    requests = []
    responses = [
        {"task": {"task_id": "shared-task", "status": "QUEUED"}},
        {
            "task": {
                "task_id": "shared-task",
                "status": "COMPLETED",
                "outcome": "Harmless test completed.",
            }
        },
    ]

    def request_json(method, path, payload, idempotency_key):
        requests.append((method, path, payload, idempotency_key))
        return responses.pop(0)

    client = VoiceCommandCenterClient(
        request_json=request_json,
        max_wait=1,
        poll_interval=0.05,
    )
    task = run(client.execute_goal("Prepare my local summary", "tool-call-1"))

    assert task["status"] == "COMPLETED"
    assert requests[0][:3] == (
        "POST",
        "/api/goals",
        {"goal": "Prepare my local summary"},
    )
    assert requests[0][3].startswith("voice-")
    assert requests[1][1] == "/api/tasks/shared-task"
    assert "Harmless test completed." in voice_result_text(task)


def test_voice_permission_result_requests_dashboard_approval_without_approving():
    pending = {
        "task_id": "permission-task",
        "status": "AWAITING_PERMISSION",
        "approvals": [{"action": "send_message"}],
    }
    client = VoiceCommandCenterClient(
        request_json=lambda *_: {"task": pending},
        max_wait=1,
    )

    task = run(client.execute_goal("Prepare and send the summary", "call-2"))
    spoken = voice_result_text(task)

    assert task["status"] == "AWAITING_PERMISSION"
    assert "needs your approval" in spoken
    assert "127.0.0.1:8765" in spoken
    assert "have not performed the action awaiting approval" in spoken


def test_voice_goal_timeout_returns_live_task_state():
    client = VoiceCommandCenterClient(
        request_json=lambda *_: {"task": {"task_id": "running-task", "status": "RUNNING"}},
        max_wait=0,
    )

    task = run(client.execute_goal("Run a slow harmless task", "call-3"))

    assert task["status"] == "RUNNING"
    assert "still running" in voice_result_text(task)
    assert "running-task" in voice_result_text(task)


def test_voice_rejects_secret_input_before_sending_to_runtime():
    calls = []
    client = VoiceCommandCenterClient(
        request_json=lambda *args: calls.append(args) or {},
    )

    task = run(client.execute_goal("Store password=my private phrase", "call-4"))

    assert task["status"] == "REJECTED"
    assert "secure sign-in" in task["message"]
    assert calls == []


def test_voice_duplicate_call_id_uses_the_same_idempotency_key():
    keys = []

    def request_json(method, _path, _payload, key):
        if method == "POST":
            keys.append(key)
        return {"task": {"task_id": "duplicate-task", "status": "COMPLETED"}}

    client = VoiceCommandCenterClient(request_json=request_json)

    run(client.execute_goal("Check local status", "same-call-id"))
    run(client.execute_goal("Check local status", "same-call-id"))

    assert keys[0] == keys[1]


def test_voice_reports_command_center_unavailable_safely():
    from dashboard.voice_client import VoiceCommandCenterError

    def unavailable(*_):
        raise VoiceCommandCenterError("The local command center is unavailable.")

    client = VoiceCommandCenterClient(request_json=unavailable)
    task = run(client.execute_goal("Check local status", "call-5"))

    assert task["status"] == "UNAVAILABLE"
    assert "unavailable" in task["message"]


def test_voice_tool_responses_redact_secret_values():
    task = {
        "task_id": "safe-task",
        "status": "FAILED",
        "error": "failed while using api_key=private-value",
    }

    spoken = voice_result_text(task)

    assert "private-value" not in spoken
    assert "[REDACTED]" in spoken


def test_jarvis_autonomous_tool_submits_once_and_returns_without_waiting_for_runtime():
    import main

    class FakeUI:
        muted = False

        def set_state(self, _state):
            pass

        def write_log(self, _message):
            pass

    class FakeVoiceClient:
        calls = []

        async def submit_goal(self, goal, call_id):
            self.calls.append((goal, call_id))
            return {"task_id": "shared-runtime-task", "status": "QUEUED"}

    class FakeFunctionCall:
        id = "voice-call-1"
        name = "autonomous_task"
        args = {"goal": "Prepare a harmless local summary"}

    jarvis = object.__new__(main.JarvisLive)
    jarvis.ui = FakeUI()
    jarvis._voice_goal_client = FakeVoiceClient()
    jarvis._voice_goal_monitors = {}
    jarvis._start_voice_goal_monitor = lambda _task_id: None

    response = run(jarvis._execute_tool(FakeFunctionCall()))

    assert jarvis._voice_goal_client.calls == [
        ("Prepare a harmless local summary", "voice-call-1")
    ]
    assert "shared local Command Center" in response.response["result"]
    assert "shared-runtime-task" in response.response["result"]


def test_jarvis_does_not_persist_credentials_through_voice_memory(monkeypatch):
    import main

    class FakeUI:
        muted = False

        def set_state(self, _state):
            pass

    class FakeFunctionCall:
        id = "memory-call-1"
        name = "save_memory"
        args = {
            "category": "credentials",
            "key": "service-token",
            "value": "private-token-value",
        }

    saved = []
    monkeypatch.setattr(main, "update_memory", lambda value: saved.append(value))
    jarvis = object.__new__(main.JarvisLive)
    jarvis.ui = FakeUI()

    response = run(jarvis._execute_tool(FakeFunctionCall()))

    assert saved == []
    assert "was not saved" in response.response["result"]
