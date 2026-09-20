# JARVIS production deployment

## Architecture

JARVIS Cloud is one authenticated FastAPI web service on Render. It serves the
existing HUD, runs Gemini planning, owns the persistent task/device/activity
state, and exposes HTTPS endpoints. A single paid Render instance is required
because the existing JSON stores and attached disk are single-writer state.
The attached `/var/data` disk persists tasks, devices, activity, learned
workflows, permissions, and uploads across restarts and deploys.

`mac-main` remains a local process. It never listens for inbound traffic; it
polls the Cloud and sends heartbeats, claims, results, and permission requests
over outbound HTTPS. The existing lease/recovery logic remains authoritative.

## Environment variables

Cloud:

- `JARVIS_ENV=production`
- `GEMINI_API_KEY` — Gemini secret, stored in Render Environment > Secret Files/Environment.
- `JARVIS_ADMIN_TOKEN` — long random token for the HUD/API operator.
- `JARVIS_AGENT_TOKEN` — separate long random token for `mac-main`.
- `JARVIS_DATA_DIR=/var/data` — Render disk mount.
- `JARVIS_CORS_ORIGINS` — optional comma-separated origins only if the HUD is hosted elsewhere.

Mac Agent:

- `JARVIS_CLOUD_URL=https://<service>.onrender.com`
- `JARVIS_AGENT_TOKEN` — the same agent secret, kept only in the Mac environment.
- `JARVIS_AGENT_ID=mac-main`
- `JARVIS_AGENT_OUTBOX_FILE` — optional local durable result outbox path.

Generate tokens locally without printing them into source control, for example:

```sh
openssl rand -hex 32
```

Do not paste secrets into chat or commit `.env` files. If the currently local
Gemini credential has ever been shared or committed, rotate it in Google AI
Studio and replace the Render secret.

## Local production-style simulation

Use the project virtual environment. In one terminal, set the variables from
`.env.example` with real local values and start the Cloud:

```sh
./.venv/bin/uvicorn cloud.server:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
JARVIS_CLOUD_URL=http://127.0.0.1:8000 \
JARVIS_AGENT_TOKEN="$JARVIS_AGENT_TOKEN" \
./.venv/bin/python agent/mac_agent.py
```

Open `http://127.0.0.1:8000/dashboard`, enter the admin token, and use the HUD.
`Open Safari` is routed by the deterministic safe-intent path; unsafe actions
still stop at `waiting_permission` until the HUD approves the exact pending
action and parameters.

## Render deployment

1. Create/connect a Git repository containing this project and choose **New >
   Blueprint** in Render.
2. Select the repository. Render reads `render.yaml`.
3. Enter `GEMINI_API_KEY`, `JARVIS_ADMIN_TOKEN`, and `JARVIS_AGENT_TOKEN` when
   prompted. Attach the one-gigabyte disk defined by the Blueprint.
4. Deploy the Blueprint. The service URL is the Cloud URL for the Mac Agent and
   HUD, for example `https://jarvis-cloud.onrender.com`.
5. Verify `/health`, `/ready`, and `/dashboard`; then set the Mac Agent variables
   and start it locally.

Render's free web service has an ephemeral filesystem and may sleep, so it is
not a valid 24/7 configuration for this project. The paid single-instance
service plus persistent disk is the smallest deployment that preserves the
existing storage architecture. A future multi-instance migration should move
tasks/devices/activity/memory/uploads to managed Postgres/object storage before
enabling horizontal scaling.

## Operations and rollback

- Stop/restart Cloud: Render service > **Manual Deploy / Restart service**.
- View logs: Render service > **Logs**.
- Update: push the tested commit to the configured branch, or use **Manual Deploy**.
- Roll back: Render service > **Deploys** > select the last known-good deploy > **Rollback**.
- Stop/restart Mac Agent: press `Ctrl-C`, then rerun `./.venv/bin/python agent/mac_agent.py`.
- Remote HUD: open the Cloud HTTPS URL followed by `/dashboard`.

Before destructive changes, export the `/var/data` disk or copy its JSON files.
The app never auto-approves permissions, and an offline Mac leaves queued tasks
in Cloud for later reconnect/recovery.
