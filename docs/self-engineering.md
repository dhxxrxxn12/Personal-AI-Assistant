# Controlled self-engineering

The self-engineering subsystem is an isolated, opt-in workflow for JARVIS to
inspect its repository, plan bounded changes, execute them in a Git worktree,
test and repair them, create a checkpoint, and report a trace. It is connected
to the existing AutonomousEngine and authenticated Cloud/HUD surfaces, while
the normal Cloud deployment and 17-action Mac Agent remain protected.

## Lifecycle

```text
GOAL → UNDERSTAND → INSPECT → GAP ANALYSIS → PLAN → APPROVAL
  → IMPLEMENT → TEST → FAILURE ANALYSIS → REPAIR → RETEST
  → GIT CHECKPOINT → SANDBOX DEPLOY → VERIFY → LEARN → REPORT
```

A capability goal uses the approval-gated layer in front of the same lifecycle:

```text
CAPABILITY GOAL → INSPECT → INVENTORY → GAP ANALYSIS → DEPENDENCY GRAPH
  → ENGINEERING ROADMAP → AWAITING_CAPABILITY_APPROVAL
  → APPROVE | MODIFY | CANCEL → (only after approval) the lifecycle above
```

`EngineeringDirector.prepare()` provides bounded inspection, evidence-based
capability analysis, strict plan validation, deterministic recovery planning,
and an auditable trace. `EngineeringDirector.execute()` coordinates the full
workflow through a unique Git worktree. It accepts only a trusted application
implementation callback; provider text is never treated as executable code or
as a shell command. Mutating plans stop at `awaiting_approval`, and successful
checkpoints stop at `awaiting_deploy_approval`; production is never merged or
deployed automatically.

## Modules

- `repository_inspector.py` uses `pathlib` and Python AST parsing. It returns a
  bounded tree, imports, classes, functions, routes, tests, configuration,
  deployment files, Git status, and recent commits. It never executes source
  code and does not read secret file contents.
- `capability_analyzer.py` maps goal terms to evidence in the snapshot. It
  reports current capabilities, missing capabilities, dependencies, and risks.
- `engineering_planner.py` accepts only the versioned JSON plan shape. Unknown
  operations, empty plans, unsafe paths, duplicate IDs, missing dependencies,
  and missing verification checks are rejected. Invalid provider output gets a
  deterministic read/test-only fallback.
- `policies.py` and the existing `PermissionManager` enforce the operation
  allowlist, risk levels, workspace boundary, and protected paths.
- `code_executor.py` performs text-only create/modify/rename/delete operations
  and returns a bounded unified diff for every successful change.
  Modifications use an inspected-content hash so a changed target is never
  blindly overwritten.
- `test_runner.py`, `failure_analyzer.py`, and `repair_engine.py` use fixed
  shell-free test/static commands, redact secret-like output, and cap repair
  attempts. A failed repair stops with evidence for human review.
- `git_manager.py` stages only explicit paths, creates normal branches and
  commits, never pushes or rewrites history, and rolls back with additive
  `git revert` only after approval.
- `sandbox_manager.py` creates and cleans unique `.jarvis-workspaces/` Git
  worktrees, validates task IDs and boundaries, and accepts only an allowlist
  of development programs. It strips known JARVIS credentials from child
  environments.
- `deployment_verifier.py` performs read-only HTTP(S) health checks.
- `diagnostics.py` reports the JARVIS version, current Git commit, supplied
  test status, and active action count without reading `.env`.
- `change_memory.py` stores engineering outcomes separately from learned task
  workflows and matches only exact normalized goal, capability, subsystem, and
  optional action keys. It redacts credential-like fields before persistence.
- `capability_builder.py` is the local, read-only capability layer: it
  inventories catalog and repository-discovered actions, resolves the
  goal-relevant dependency graph, separates AVAILABLE, PARTIAL, MISSING,
  PLANNED, and BLOCKED capabilities, derives dependency-ordered engineering
  phases, and stops at `AWAITING_CAPABILITY_APPROVAL`. It never edits source.
- `capability_store.py` persists capability plans and the last registry snapshot
  to the existing local runtime path with atomic writes and credential
  redaction, so approval state survives a restart without a remote database.
- `capability_demo.py` is the local command-line demonstration of the analysis
  and the approval gate.

## Local capability builder

The capability layer runs entirely on the local machine and has no Cloud
dependency: no deployed API, no remote persistence, no Cloud authentication, and
no Cloud endpoint is required. Capabilities are discovered from the repository
itself — every `actions/*.py` module that declares a module-level `TOOL` becomes
a discovered capability, and `docs/proactive-roadmap.json` marks documented but
unimplemented capabilities as `planned` — rather than being hardcoded.

Capability status is always evidence-backed:

| Status | Meaning |
| --- | --- |
| `available` | every declared evidence path exists in the local tree |
| `partial` | some evidence exists, but the capability boundary is incomplete |
| `planned` | no local evidence, but the local roadmap document tracks it |
| `missing` | no local evidence and no roadmap entry |
| `blocked` | local permission policy or an undefined prerequisite prevents progress |

The dependency graph is deterministic and dependency-first. Cycles and
undefined dependencies are reported as `blocked` nodes with explicit reasons
instead of raising or being silently dropped. The roadmap is grouped into
dependency-ordered phases, each with the permissions and tests required before
the next phase starts.

Nothing is implemented before approval. `approve`, `modify` (defer capabilities
and record an operator note), and `cancel` are the only operator decisions;
`EngineeringDirector.execute` refuses to run without an approved plan and keeps
the work inside a disposable Git worktree. Planned verification tests are
selected from *tracked* repository files, because the sandbox is a worktree of
committed content.

## Approval and protected data

Read-only inspection, source reads, tests, static checks, diff inspection,
deployment verification, and memory recording are low risk. File mutation,
branch creation, and sandbox deployment are medium risk. Commits and rollback
are high risk. Production deployment is critical and is rejected by this
foundation's policy.

The policy rejects paths outside the workspace and protects `.git`, virtual
environments, `.env` files other than `.env.example`, API key files, persistent
task/device/activity stores, learned permissions/memory, agent outbox data,
uploads, and Python caches. No LLM-generated shell command is executed.

## Local supervised use

Capability analysis needs no environment flag and performs no mutation:

```sh
# Inspect, inventory, gap-analyse, build the graph, and stop at the gate.
PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo \
  "Build a proactive JARVIS morning briefing system."

# Same goal, machine-readable plan; then list persisted plans.
PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo --json
PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo --list

# Operator decisions (state only; still no code change).
PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo --approve
PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo --defer calendar_integration --approve
PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo --cancel "not this week"

# Only after approval: run the roadmap through the local sandbox with placeholder
# modules, which stops before any deployment and leaves production untouched.
export JARVIS_SELF_ENGINEERING_ENABLED=1
PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo \
  "Build a proactive JARVIS morning briefing system." --approve --dry-run-handoff
```

Approval state and the last registry snapshot are stored locally in
`automation/capability_plans.json` and `automation/capability_registry.json`
(or under `JARVIS_DATA_DIR` when a deployment configures one); both files are
git-ignored runtime state and contain no source contents or secrets.

```sh
export JARVIS_SELF_ENGINEERING_ENABLED=1
PYTHONPATH=. .venv/bin/python -c \
  'from automation.self_engineering import EngineeringDirector; print(EngineeringDirector(".", enabled=True).prepare("Audit the repository").to_dict()["status"])'
```

Keep the subsystem disabled in production. Before any future implementation
run, review the plan, approve only the named steps, inspect every diff, run the
full test suite, checkpoint on a task branch, and verify a separate sandbox.
If verification fails, stop and use the recorded trace and Git checkpoint for
human-directed rollback; do not force-push or reset shared history.

## Cloud and HUD

Authenticated administrators can use `POST /engineering/tasks` and inspect
tasks/events with the corresponding `GET` endpoints. Approval, cancellation,
and sandbox rollback are explicit admin operations. Engineering events reuse
the existing Cloud activity stream, and the command center includes a JARVIS
Engineering panel showing phase, files, tests, repairs, sandbox state,
checkpoint, and production impact.

## Recovery and disablement

Set `JARVIS_SELF_ENGINEERING_ENABLED=0` or unset it to disable the director's
planning entrypoint. A repair loop stops after its configured maximum and
returns failure logs, redacted relevant output, attempted repairs, and the
current result. A Git rollback is an explicit additive revert, not a history
rewrite. Persistent runtime data is never an automatic cleanup target.

## First controlled engineering proof

`tests/test_self_engineering.py` runs one low-risk end-to-end task against a
temporary Git repository. It proves `GOAL → INSPECT → PLAN → SANDBOX →
IMPLEMENT → TEST → VERIFY → GIT CHECKPOINT → REPORT`, then asserts the
production fixture remains unchanged. The read-only diagnostic capability is
available through `/engineering/diagnostics`, and the proactive capability map
is stored in `docs/proactive-roadmap.json`.
