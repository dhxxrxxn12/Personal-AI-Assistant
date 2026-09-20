"""Focused contracts for Cloud planning diagnostics and browser voice auth."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from automation.autonomous_planner import AutonomousPlanner, PlannerError
from cloud import auth, server, voice as voice_module
from cloud.voice import BrowserVoiceSession, VoiceTicketStore


class _Registry:
    def names(self):
        return ["open_app"]

    def has(self, name):
        return name == "open_app"

    def get_tool_declarations(self):
        return [{
            "name": "open_app",
            "description": "Open a safe local application.",
            "parameters": {"type": "OBJECT"},
        }]


class _Memory:
    def find_by_goal(self, goal):
        return []

    def names(self):
        return []


class _Models:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _planner(response=None, error=None):
    planner = AutonomousPlanner(
        action_registry=_Registry(),
        learning_memory=_Memory(),
        logger=lambda *_args: None,
    )
    planner.client = SimpleNamespace(models=_Models(response=response, error=error))
    return planner


def test_planner_requests_structured_json_and_builds_a_real_plan():
    response = SimpleNamespace(text='{"tasks":[{"action":"open_app","parameters":{"app_name":"Calculator"}}]}')
    planner = _planner(response=response)

    plan = planner.create_plan_with_gemini("open Calculator")

    assert plan.source == "gemini"
    assert plan.tasks[0].action == "open_app"
    request = planner.client.models.calls[0]
    assert request["config"].response_mime_type == "application/json"
    assert request["config"].response_schema["required"] == ["tasks"]


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ('{"tasks":[]}', "empty_plan"),
        ("not-json SECRET_PROVIDER_PAYLOAD", "invalid_json"),
    ],
)
def test_planner_invalid_or_empty_responses_are_safe(body, kind):
    planner = _planner(response=SimpleNamespace(text=body))

    with pytest.raises(PlannerError) as raised:
        planner.create_plan_with_gemini("do something")

    assert raised.value.kind == kind
    assert "SECRET_PROVIDER_PAYLOAD" not in str(raised.value)
    assert "Response:" not in str(raised.value)


def test_planner_reports_quota_without_provider_exception_text():
    planner = _planner(error=RuntimeError("429 quota SECRET_PROVIDER_PAYLOAD"))

    with pytest.raises(PlannerError) as raised:
        planner.create_plan_with_gemini("do something")

    assert raised.value.kind == "quota"
    assert str(raised.value) == "Gemini quota or rate limit reached."
    assert "SECRET_PROVIDER_PAYLOAD" not in str(raised.value)


def test_planner_missing_credentials_is_clear():
    planner = AutonomousPlanner(
        action_registry=_Registry(),
        learning_memory=_Memory(),
        logger=lambda *_args: None,
    )

    with pytest.raises(PlannerError, match="credentials are not configured") as raised:
        planner.create_plan_with_gemini("do something")
    assert raised.value.kind == "credentials"


def test_voice_tickets_expire_and_are_single_use():
    store = VoiceTicketStore(ttl_seconds=10)
    ticket, expires = store.issue()

    assert ticket
    assert expires == 10
    assert store.consume(ticket) is True
    assert store.consume(ticket) is False


def test_voice_session_requires_admin_and_does_not_require_local_env_file():
    original = auth.settings
    auth.settings = replace(
        original,
        environment="production",
        gemini_api_key="configured-for-test",
        admin_token="admin-test-token",
        agent_token="agent-test-token",
    )
    try:
        with TestClient(server.app) as client:
            assert client.post("/voice/session").status_code == 401
            response = client.post(
                "/voice/session",
                headers={"Authorization": "Bearer admin-test-token"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["ticket"]
            assert payload["expires_in"] > 0
            assert "configured-for-test" not in response.text
    finally:
        auth.settings = original


def test_invalid_voice_ticket_is_rejected_before_gemini_connect():
    with TestClient(server.app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/voice?ticket=invalid-ticket"):
                pass


def test_voice_without_credentials_returns_safe_error():
    class _Socket:
        def __init__(self):
            self.messages = []
            self.closed = None

        async def send_json(self, payload):
            self.messages.append(payload)

        async def close(self, code):
            self.closed = code

    socket = _Socket()
    asyncio.run(BrowserVoiceSession(
        api_key=None,
        submit_task=lambda _goal: None,
    ).run(socket))

    assert socket.messages == [{
        "type": "error",
        "code": "credentials",
        "detail": "Gemini voice credentials were rejected or are missing.",
    }]
    assert socket.closed == 1011


def test_voice_session_streams_audio_and_returns_tool_result(monkeypatch):
    class _Socket:
        def __init__(self):
            self.messages = [
                {"type": "websocket.receive", "bytes": b"\x00\x01"},
                {"type": "websocket.receive", "text": json.dumps({"type": "stop"})},
            ]
            self.json_messages = []
            self.audio_messages = []

        async def receive(self):
            await asyncio.sleep(0)
            return self.messages.pop(0)

        async def send_json(self, payload):
            self.json_messages.append(payload)

        async def send_bytes(self, payload):
            self.audio_messages.append(payload)

        async def close(self, code):
            return None

    class _LiveSession:
        def __init__(self):
            self.audio = []
            self.tool_responses = []

        async def send_realtime_input(self, **kwargs):
            self.audio.append(kwargs)

        async def send_tool_response(self, **kwargs):
            self.tool_responses.append(kwargs)

        async def receive(self):
            yield SimpleNamespace(
                data=b"\x02\x03",
                server_content=None,
                tool_call=SimpleNamespace(function_calls=[SimpleNamespace(
                    id="call-1",
                    name="submit_task",
                    args={"goal": "Open Calculator"},
                )]),
            )
            await asyncio.sleep(1)

    class _Connect:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *_args):
            return None

    live_session = _LiveSession()

    class _Client:
        class _Aio:
            class _Live:
                @staticmethod
                def connect(**_kwargs):
                    return _Connect(live_session)

            live = _Live()

        aio = _Aio()

    monkeypatch.setattr(voice_module.genai, "Client", lambda **_kwargs: _Client())
    queued = []

    async def submit_task(goal):
        queued.append(goal)
        return {"id": "task-1", "status": "planning"}

    socket = _Socket()
    asyncio.run(BrowserVoiceSession(
        api_key="configured-for-test",
        submit_task=submit_task,
    ).run(socket))

    assert live_session.audio[0]["audio"].data == b"\x00\x01"
    assert socket.audio_messages == [b"\x02\x03"]
    assert queued == ["Open Calculator"]
    assert live_session.tool_responses[0]["function_responses"][0].response["result"] == "queued"
