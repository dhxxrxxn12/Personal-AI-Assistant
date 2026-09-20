# Controlled self-engineering

The self-engineering subsystem is an isolated, opt-in foundation for JARVIS to
inspect its repository, plan bounded changes, validate them, and report a
trace. It is deliberately not wired into the existing Cloud task route or Mac
Agent loop yet. The current Cloud deployment and the 17-action Mac Agent remain
the production execution path.

## Lifecycle

```text
GOAL → UNDERSTAND → INSPECT → GAP ANALYSIS → PLAN → APPROVAL
  → IMPLEMENT → TEST → FAILURE ANALYSIS → REPAIR → RETEST
  → GIT CHECKPOINT → SANDBOX DEPLOY → VERIFY → LEARN → REPORT
```

`EngineeringDirector.prepare()` currently implements the safe first half:
bounded inspection, evidence-based capability analysis, strict plan validation,
deterministic recovery planning, and an auditable trace. Implementation,
checkpoint, sandbox, and memory APIs are separate so a future orchestrator must
make each approval boundary explicit.

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
- `sandbox_manager.py` executes only preconfigured argument arrays in a
  directory different from the production workspace. It strips known JARVIS
  credentials from the child environment.
- `deployment_verifier.py` performs read-only HTTP(S) health checks.
- `change_memory.py` stores engineering outcomes separately from learned task
  workflows and matches only exact normalized goal, capability, subsystem, and
  optional action keys. It redacts credential-like fields before persistence.

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

## Recovery and disablement

Set `JARVIS_SELF_ENGINEERING_ENABLED=0` or unset it to disable the director's
planning entrypoint. A repair loop stops after its configured maximum and
returns failure logs, redacted relevant output, attempted repairs, and the
current result. A Git rollback is an explicit additive revert, not a history
rewrite. Persistent runtime data is never an automatic cleanup target.

## First intended engineering goal

The first real goal should be an analysis-only request for the proactive JARVIS
experience: capability inventory, architecture mapping, missing capabilities,
dependencies, privacy/security risks, implementation plan, test plan, and
sandbox deployment plan. It must be reviewed before implementing scheduler,
context, briefing, voice, calendar/email, notification, or telephony features.
