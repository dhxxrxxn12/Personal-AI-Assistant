"""Browser voice gateway for the Cloud command center.

The browser receives a short-lived, one-use ticket from an authenticated HTTP
request.  The Gemini credential never crosses the WebSocket boundary and the
Mac Agent remains the only component that performs desktop actions.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types


logger = logging.getLogger("jarvis.cloud.voice")

VOICE_MODEL = (
    os.getenv("JARVIS_LIVE_MODEL", "gemini-3.8-live").strip()
    or "gemini-3.8-live"
)
VOICE_API_VERSION = os.getenv("JARVIS_LIVE_API_VERSION", "v1beta").strip() or "v1beta"
VOICE_TICKET_TTL_SECONDS = 60
MAX_VOICE_GOAL_LENGTH = 5000


@dataclass(frozen=True)
class _VoiceTicket:
    expires_at: float


class VoiceTicketStore:
    """Small in-memory one-time ticket store suitable for one Cloud instance."""

    def __init__(self, ttl_seconds: int = VOICE_TICKET_TTL_SECONDS) -> None:
        self.ttl_seconds = max(10, int(ttl_seconds))
        self._tickets: dict[str, _VoiceTicket] = {}
        self._lock = threading.Lock()

    def issue(self) -> tuple[str, int]:
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge(now)
            self._tickets[token] = _VoiceTicket(now + self.ttl_seconds)
        return token, self.ttl_seconds

    def consume(self, token: str) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            ticket = self._tickets.pop(token, None)
        return ticket is not None and ticket.expires_at > now

    def _purge(self, now: float) -> None:
        expired = [key for key, ticket in self._tickets.items() if ticket.expires_at <= now]
        for key in expired:
            self._tickets.pop(key, None)


voice_tickets = VoiceTicketStore()


def _provider_failure_code(exc: Exception) -> str:
    text = str(exc).lower()
    if any(marker in text for marker in ("401", "403", "api key", "unauthenticated", "permission")):
        return "credentials"
    if any(marker in text for marker in ("429", "quota", "resource_exhausted", "rate limit")):
        return "quota"
    if any(marker in text for marker in ("404", "not found", "unknown model")):
        return "model_unavailable"
    if any(marker in text for marker in ("503", "unavailable", "timeout", "timed out")):
        return "service_unavailable"
    return "live_unavailable"


def _provider_failure_message(code: str) -> str:
    return {
        "credentials": "Gemini voice credentials were rejected or are missing.",
        "quota": "Gemini voice quota or rate limit reached.",
        "model_unavailable": "Configured Gemini Live voice model is unavailable.",
        "service_unavailable": "Gemini Live voice service is temporarily unavailable.",
        "live_unavailable": "Gemini Live voice is temporarily unavailable.",
    }.get(code, "Gemini Live voice is unavailable.")


class BrowserVoiceSession:
    """Bridge browser PCM frames and Gemini Live for one authenticated socket."""

    def __init__(
        self,
        api_key: Optional[str],
        submit_task: Callable[[str], Awaitable[dict[str, Any]]],
        model: str = VOICE_MODEL,
    ) -> None:
        self.api_key = api_key
        self.submit_task = submit_task
        self.model = model
        self._websocket: Optional[WebSocket] = None
        self._send_lock = asyncio.Lock()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._websocket is None:
            return
        async with self._send_lock:
            await self._websocket.send_json(payload)

    async def _send_error(self, code: str) -> None:
        await self._send_json({
            "type": "error",
            "code": code,
            "detail": _provider_failure_message(code),
        })

    async def run(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        if not self.api_key:
            await self._send_error("credentials")
            await websocket.close(code=1011)
            return

        try:
            client = genai.Client(
                api_key=self.api_key,
                http_options={"api_version": VOICE_API_VERSION},
            )
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                input_audio_transcription={},
                output_audio_transcription={},
                system_instruction=(
                    "You are JARVIS, a concise voice assistant in the JARVIS command center. "
                    "Speak naturally and briefly. For a desktop request, call submit_task "
                    "with the user's exact goal so the authenticated Mac Agent can execute "
                    "it under its existing permission policy. Never claim a desktop action "
                    "was completed merely because it was queued."
                ),
                tools=[{
                    "function_declarations": [{
                        "name": "submit_task",
                        "description": "Queue one desktop goal for the authenticated Mac Agent.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "goal": {
                                    "type": "STRING",
                                    "description": "The user's desktop command, without extra commentary.",
                                },
                            },
                            "required": ["goal"],
                        },
                    }],
                }],
            )

            async with client.aio.live.connect(model=self.model, config=config) as session:
                await self._send_json({
                    "type": "state",
                    "state": "LISTENING",
                    "model": self.model,
                })
                browser_task = asyncio.create_task(self._read_browser(websocket, session))
                model_task = asyncio.create_task(self._read_model(websocket, session))
                done, pending = await asyncio.wait(
                    {browser_task, model_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    error = task.exception()
                    if error and not isinstance(error, WebSocketDisconnect):
                        raise error
        except WebSocketDisconnect:
            return
        except Exception as exc:
            code = _provider_failure_code(exc)
            logger.warning(
                "browser voice session ended code=%s exception=%s",
                code,
                type(exc).__name__,
            )
            try:
                await self._send_error(code)
                await websocket.close(code=1011)
            except Exception:
                pass
        finally:
            self._websocket = None

    async def _read_browser(self, websocket: WebSocket, session: Any) -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))

            audio = message.get("bytes")
            if audio:
                await session.send_realtime_input(
                    audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
                )
                continue

            raw = message.get("text")
            if not raw:
                continue
            try:
                payload = __import__("json").loads(raw)
            except (TypeError, ValueError):
                continue
            kind = payload.get("type") if isinstance(payload, dict) else None
            if kind == "stop":
                return
            if kind == "interrupt":
                await self._send_json({"type": "state", "state": "LISTENING"})
                try:
                    await session.send_realtime_input(activity_start=types.ActivityStart())
                except Exception:
                    # Audio continues to work on SDK versions without activity markers.
                    pass
                continue
            if kind == "text":
                text = str(payload.get("text", "")).strip()[:MAX_VOICE_GOAL_LENGTH]
                if text:
                    await session.send_client_content(
                        turns={"role": "user", "parts": [{"text": text}]},
                        turn_complete=True,
                    )

    async def _read_model(self, websocket: WebSocket, session: Any) -> None:
        async for response in session.receive():
            audio = getattr(response, "data", None)
            if audio:
                await self._send_json({"type": "state", "state": "SPEAKING"})
                await websocket.send_bytes(audio)

            server_content = getattr(response, "server_content", None)
            if server_content is not None:
                input_transcription = getattr(server_content, "input_transcription", None)
                input_text = getattr(input_transcription, "text", None)
                if input_text:
                    await self._send_json({
                        "type": "transcript",
                        "speaker": "user",
                        "text": str(input_text),
                        "final": bool(getattr(server_content, "turn_complete", False)),
                    })
                    await self._send_json({"type": "state", "state": "THINKING"})

                output_transcription = getattr(server_content, "output_transcription", None)
                output_text = getattr(output_transcription, "text", None)
                if output_text:
                    await self._send_json({
                        "type": "transcript",
                        "speaker": "jarvis",
                        "text": str(output_text),
                        "final": bool(getattr(server_content, "turn_complete", False)),
                    })
                    await self._send_json({"type": "state", "state": "SPEAKING"})

                if getattr(server_content, "turn_complete", False):
                    await self._send_json({"type": "state", "state": "LISTENING"})

            tool_call = getattr(response, "tool_call", None)
            function_calls = getattr(tool_call, "function_calls", None) if tool_call else None
            if function_calls:
                function_responses = []
                for function_call in function_calls:
                    name = getattr(function_call, "name", "")
                    args = getattr(function_call, "args", {}) or {}
                    if name != "submit_task" or not isinstance(args, dict):
                        function_responses.append(types.FunctionResponse(
                            id=getattr(function_call, "id", None),
                            name=name or "unknown",
                            response={"error": "Unsupported voice function."},
                        ))
                        continue
                    goal = str(args.get("goal", "")).strip()[:MAX_VOICE_GOAL_LENGTH]
                    if not goal:
                        function_responses.append(types.FunctionResponse(
                            id=getattr(function_call, "id", None),
                            name=name,
                            response={"error": "A non-empty desktop goal is required."},
                        ))
                        continue
                    task = await self.submit_task(goal)
                    function_responses.append(types.FunctionResponse(
                        id=getattr(function_call, "id", None),
                        name=name,
                        response={
                            "result": "queued",
                            "task_id": str(task.get("id", "")),
                            "status": str(task.get("status", "planning")),
                        },
                    ))
                await session.send_tool_response(function_responses=function_responses)
