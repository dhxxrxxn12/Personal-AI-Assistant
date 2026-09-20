# JARVIS deployment on Blitz Cloud

## Runtime architecture

The Blitz service runs `cloud.server:app`, an authenticated FastAPI HTTP
service. It serves the existing command-center HUD, plans tasks with Gemini,
stores cloud task/device/activity/learning state, and queues work for the
locally running Mac Agent.

The Mac Agent remains a separate process on the Mac. It connects outbound to
the Blitz HTTPS URL, registers and heartbeats with the Cloud, polls and claims
tasks, executes local actions, and reports results. The cloud container does
not start `main.py`, `ui.py`, or `agent/mac_agent.py`, and does not attempt to
use Safari, a display, a microphone, a camera, keyboard, mouse, or screenshots.

Blitz terminates HTTPS. The container only serves HTTP on the platform-provided
`PORT`; the Docker command always binds `0.0.0.0`.

## Blitz container

Use the repository `Dockerfile`. It targets `linux/amd64`, uses Python 3.11,
installs the pinned `requirements-cloud.txt` set, serves the existing HUD, and
runs as the non-root `jarvis` user. `PORT` defaults to `8080` when the platform
does not inject it.

The old `render.yaml` is retained for Render compatibility and is not the Blitz
deployment entrypoint.

Recommended platform settings:

- Build from the repository `Dockerfile`.
- Expose/container port: `8080` (or the value supplied through `PORT`).
- Start command: use the Dockerfile default; do not add nginx or another TLS
  server.
- Health check: `GET /health`.
- One service instance is recommended because the current JSON stores are
  single-writer state.

## Environment variables

### Minimum Cloud configuration

Set these in Blitz's secret/environment settings; never commit their values:

- `GEMINI_API_KEY` — required secret (A), used by the Cloud planner.
- `JARVIS_ADMIN_TOKEN` — required secret (A), used by the HUD/operator API.
- `JARVIS_AGENT_TOKEN` — required secret (A), used by the Mac Agent API.
- `JARVIS_ENV=production` — required deployment configuration (B); this makes
  startup reject an incomplete secret configuration.
- `JARVIS_DATA_DIR` — required deployment configuration (B) when persistent
  storage is available; set it to the mounted writable data directory. The
  image default is `/var/lib/jarvis`.
- `PORT` — Blitz deployment configuration (B). Blitz normally injects it; the
  application fallback is `8080`.

Use different long random values for the admin and agent tokens. Generate them
locally, for example with `openssl rand -hex 32`, and do not print them in
logs or paste them into source control.

### Complete variable classification

| Variable | Class | Cloud/agent requirement |
|---|---|---|
| `GEMINI_API_KEY` | A — required secret | Required by production Cloud startup. |
| `JARVIS_ADMIN_TOKEN` | A — required secret | Required for HUD/operator routes. |
| `JARVIS_AGENT_TOKEN` | A — required secret | Required for Mac Agent routes. |
| `JARVIS_CLOUD_TOKEN` | F — unnecessary for this deployment | Legacy shared-token fallback. Do not set when using separate admin and agent tokens. |
| `JARVIS_CLOUD_URL` | D — Mac-Agent-only | Set locally to `https://<actual-blitz-host>`; no safe production default. |
| `JARVIS_CORS_ORIGINS` | C — optional with safe default | Leave empty for the same-origin HUD; otherwise use a comma-separated allowlist. |
| `JARVIS_ENV` | B — required deployment configuration | Set `production`. Default `development` is only for local work. |
| `JARVIS_HOST` | F — unnecessary for this deployment | The container command fixes the bind address to `0.0.0.0`. |
| `JARVIS_PORT` | C — optional legacy setting | `PORT` takes precedence; fallback is `8080`. |
| `PORT` | B — platform deployment configuration | Injected by Blitz; fallback is `8080`. |
| `JARVIS_DATA_DIR` | B — required deployment configuration | Set to the writable persistent mount, normally `/var/lib/jarvis` in the image. |
| `JARVIS_PLANNER_MODEL` | C — optional Cloud configuration | Planner model override; defaults to `gemini-flash-latest`. |
| `JARVIS_LIVE_MODEL` | C — optional Cloud configuration | Browser voice model override; defaults to `gemini-3.8-live`. |
| `JARVIS_LIVE_API_VERSION` | C — optional Cloud configuration | Gemini Live API version; defaults to `v1beta`. |
| `JARVIS_SELF_ENGINEERING_ENABLED` | C — local supervised development only | Defaults to `0`; keep disabled in production until a separate approval workflow is deployed. |
| `JARVIS_AGENT_ID` | D — Mac-Agent-only | Optional; defaults to `mac-main`. |
| `JARVIS_AGENT_CONNECT_TIMEOUT` | D — Mac-Agent-only | Optional; default `10` seconds. |
| `JARVIS_AGENT_LEASE_HEARTBEAT_SECONDS` | D — Mac-Agent-only | Optional; default `15` seconds. |
| `JARVIS_AGENT_OUTBOX_FILE` | D — Mac-Agent-only | Optional; defaults to `agent/mac_agent_outbox.json` on the Mac. |
| `JARVIS_AGENT_POLL_SECONDS` | D — Mac-Agent-only | Optional; default `3` seconds. |
| `JARVIS_AGENT_POOL_TIMEOUT` | D — Mac-Agent-only | Optional; default `10` seconds. |
| `JARVIS_AGENT_READ_TIMEOUT` | D — Mac-Agent-only | Optional; default `30` seconds. |
| `JARVIS_AGENT_WRITE_TIMEOUT` | D — Mac-Agent-only | Optional; default `30` seconds. |
| `APPDATA` | E — Windows-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `LOCALAPPDATA` | E — Windows-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `PROGRAMDATA` | E — Windows-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `PROGRAMFILES` | E — Windows-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `XDG_CURRENT_DESKTOP` | E — desktop-agent-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `XDG_DESKTOP_DIR` | E — desktop-agent-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `XDG_DOCUMENTS_DIR` | E — desktop-agent-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `XDG_DOWNLOAD_DIR` | E — desktop-agent-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `XDG_MUSIC_DIR` | E — desktop-agent-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `XDG_PICTURES_DIR` | E — desktop-agent-only/irrelevant to Linux Cloud | Not required by the Cloud. |
| `XDG_VIDEOS_DIR` | E — desktop-agent-only/irrelevant to Linux Cloud | Not required by the Cloud. |

The Cloud also accepts optional `JARVIS_LOG_LEVEL`; it defaults to `INFO` and
is not required for deployment. Optional model overrides are
`JARVIS_PLANNER_MODEL` (default `gemini-flash-latest`), `JARVIS_LIVE_MODEL`
(default `gemini-3.8-live`), and `JARVIS_LIVE_API_VERSION`
(default `v1beta`). These values are configuration, not credentials.

## Controlled self-engineering

The isolated `automation/self_engineering/` subsystem is disabled by default
and is not part of the Cloud request path. Enable it only in a supervised local
workspace with `JARVIS_SELF_ENGINEERING_ENABLED=1`. Production deployment,
credential changes, external communication, and persistent-data deletion remain
outside its automatic authority. Review its trace and diff, create a checkpoint,
and verify a separate sandbox before considering any production action.

## Persistent data

The existing persistence system uses atomic JSON files and a directory for
uploads. Set `JARVIS_DATA_DIR` to a writable persistent mount to retain:

- `tasks.json` — task history, plans, leases, permission state, and results.
- `devices.json` — Mac Agent registration and last-heartbeat state.
- `activity.json` — bounded activity history and the SSE resume cursor.
- `learning_memory.json` — learned successful workflows.
- `permissions.json` — autonomous-action permission decisions.
- `uploads/` — files dropped through the HUD.

These are user/task data, not a secret store. Do not put API keys or tokens in
the data directory. The image creates `/var/lib/jarvis` and gives it to the
non-root service user, but a platform-mounted volume must itself be writable by
that user. If Blitz free-tier storage is ephemeral or unavailable, the service
still runs but those files can be lost on restart; Blitz storage is not treated
as a backup. Export/copy the directory before destructive operations.

The Mac Agent's `JARVIS_AGENT_OUTBOX_FILE` is local Mac state and is not part of
the Cloud volume. Desktop `memory/long_term.json` is also local-agent state.

## Mac Agent production connection

After Blitz provides the final HTTPS host, configure the Mac locally without
hard-coding the host in source code:

```sh
export JARVIS_CLOUD_URL="https://<actual-blitz-host>"
export JARVIS_AGENT_TOKEN="<the-same-agent-secret-configured-in-blitz>"
export JARVIS_AGENT_ID="mac-main"
python agent/mac_agent.py
```

The existing agent registers, sends heartbeats, polls and claims work, renews
task leases, retries its durable result outbox, and re-registers after a Cloud
restart or network interruption.

## HUD, authentication, and SSE

The HUD is served by the Cloud at `/dashboard`; it is not a separate desktop
server. `/health` is public and lightweight, while operator and agent routes
retain the existing bearer-token roles. The server does not log authorization
headers or token values.

Activity streaming remains `GET /activity/stream`. It sends
`text/event-stream`, keepalive comments, no-cache/no-buffering headers, and
resumes from the client's activity cursor. The browser sends the bearer token,
reconnects using its latest cursor after a dropped connection, and continues
polling `/activity` as a safety fallback. Same-origin deployment needs no CORS
setting; an externally hosted HUD needs `JARVIS_CORS_ORIGINS`.

## Browser voice

The command-center Speech panel requests a short-lived one-use ticket through
`POST /voice/session` using the existing admin bearer session, then opens the
same-origin `wss://.../voice?ticket=...` socket. The browser streams mono PCM
microphone frames at 16 kHz; Cloud streams 24 kHz PCM audio and user/JARVIS
transcripts back. The Gemini key is used only inside Cloud and is never sent to
JavaScript or the browser.

Voice-created desktop commands call the same authenticated task queue as typed
commands and target `mac-main`; the Mac Agent permission policy remains the
execution boundary. The panel exposes listening, thinking, speaking, error,
stop, and interruption states. Tickets expire quickly and are consumed once,
so a reconnect obtains a fresh ticket. If Live is unavailable, the panel falls
back to the browser Web Speech one-shot recognizer; typed commands and the
activity stream continue to work independently.

## Local production-style check

With the project virtual environment and test values configured:

```sh
PYTHONPATH=. ./.venv/bin/uvicorn cloud.server:app --host 0.0.0.0 --port 8080
```

Then open `http://127.0.0.1:8080/dashboard` locally and use the admin token.
For a local Mac Agent, point `JARVIS_CLOUD_URL` at that URL. The localhost URL
is for local simulation only; production uses the Blitz HTTPS host.

## Verification

Run the existing suite with the repository on `PYTHONPATH`:

```sh
PYTHONPATH=. ./.venv/bin/pytest -q
```

The cloud image does not install desktop/audio dependencies from the full
`requirements.txt`; those remain for the local Mac Agent and desktop app.
