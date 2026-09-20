# Repository audit

Audit baseline: 20 September 2026, commit `9a3b1f8`.

## Inventory

| Area | Contents | Decision |
| --- | --- | --- |
| `cloud/` | Authenticated FastAPI service, JSON persistence, activity/telemetry, browser voice, uploads | Keep; active production surface |
| `agent/` | Authenticated Mac Agent, heartbeat, task polling, result outbox | Keep; active device surface |
| `automation/` | Planner, engine, permissions, learned workflows | Keep active modules; remove verified snapshots |
| `actions/`, `core/`, `memory/`, `infrastructure/` | Action implementations, loaders, local runtime, memory, deployment helpers | Keep; imported by desktop/cloud paths or packaged deployment |
| `dashboard/` | Cloud-served HUD and legacy local dashboard server/assets | Keep served assets and server; remove only the unserved HTML snapshot |
| `tests/` | Cloud, voice, command-center, recovery, and hardening regression tests | Keep |
| `Dockerfile`, `render.yaml`, `requirements*.txt`, `DEPLOYMENT.md` | Deployment/build configuration and operating instructions | Keep; deployment files are intentionally distinct |
| `*.json` runtime stores | Tasks, devices, activity, permissions, learned memory, uploads | Keep out of Git; these are runtime state, not disposable source |
| `.venv/` | Local Python environment | Keep local; ignored and not part of the repository payload |

## Cleanup report

### KEEP

- `cloud/server.py` and `cloud/voice.py`: imported by the service and covered by the cloud/voice tests.
- `automation/autonomous_planner.py`: imported by `cloud.server`, `main.py`, and planner tests.
- `automation/autonomous_engine.py`, `permission_manager.py`, and `learning_memory.py`: imported by the active planner/engine and cloud task flow.
- `dashboard/static/`: mounted/served by the active cloud server; `jarvis-command-center.html` is the selected command-center entrypoint.
- Persistent runtime files under `cloud/` and `automation/`: required by the current runtime path/persistence architecture and ignored by Git.

### REMOVE

- `automation/autonomous_planner.backup.py`: tracked snapshot from `0f81fb8`, no imports/references, duplicated by `autonomous_planner.before_router.py`.
- `automation/autonomous_planner.before_router.py`: tracked snapshot from `0f81fb8`, no imports/references, byte-for-byte duplicate of the other planner snapshot.
- `cloud/server.py.backup`: older tracked server snapshot from `0f81fb8`, not imported or copied by the active Dockerfile.
- `cloud/server.py.save`: minimal early server prototype from `0f81fb8`, not imported or copied by the active deployment.
- `cloud/server.py.stage5-backup`: older tracked server snapshot from `0f81fb8`, not imported or copied by the active Dockerfile.
- `dashboard/static/jarvis-command-center.prev.html`: unserved tracked HTML snapshot; the active route serves `jarvis-command-center.html`.
- `mark_60_status.txt`: unreferenced scratch note, not used by runtime, tests, or deployment.

### GENERATED / IGNORED

- Repository-local `__pycache__/`, `*.pyc`, `.pytest_cache/`, and `.DS_Store` files: generated locally and removed from the working tree.
- `.venv/`: local environment retained, never included in the repository or Docker build.
- Runtime JSON stores and local outbox files, including `automation/engineering_memory.json`: retained on disk where present, ignored because they contain user/task state.

No persistent runtime data, production modules, active actions, configuration secrets, deployment files, or Mac Agent files were removed.
