# Replanning and Recovery

## Purpose

The local replanning layer is a recovery-oriented planning component, not a second execution engine. It analyzes failures that occur after the runtime has already launched the workflow and decides whether the workflow should retry, pause for permission, replan, or stop safely.

## Architecture

User goal
  -> Goal decomposition
  -> GoalPlan
  -> AutonomousRuntime
  -> WorkflowEngine
  -> Action execution
  -> Failure
  -> FailureClassifier
  -> RecoveryPolicy
  -> Replanner
  -> Runtime/Workflow resume or stop
  -> LearningMemory

The runtime remains the authority for orchestration, execution, and state transitions. The replanning layer only analyzes failures and produces a decision.

## Failure categories

The local failure model is deterministic and bounded. It includes:

- TRANSIENT
- PERMISSION_REQUIRED
- CAPABILITY_MISSING
- INVALID_STATE
- DEPENDENCY_BLOCKED
- ACTION_FAILED
- VERIFICATION_FAILED
- PLAN_INVALID
- PERMANENT_FAILURE
- UNKNOWN

Unknown failures fail closed. A failure classification never guesses that an unknown issue is safe to retry.

## Failure classifier

`FailureClassifier` inspects the raw failure payload and maps it to a single primary category. The logic uses deterministic keyword matching and does not inspect secrets or credentials.

## Recovery policy

`RecoveryPolicy` converts a failure category into a bounded recovery decision:

- TRANSIENT -> bounded retry, then replan if the retry budget is exceeded
- PERMISSION_REQUIRED -> pause and preserve workflow state
- CAPABILITY_MISSING -> block unless a safe, existing alternative is already known
- INVALID_STATE / DEPENDENCY_BLOCKED / PLAN_INVALID -> re-evaluate remaining objectives
- VERIFICATION_FAILED -> require re-verification or a safe fallback path
- PERMANENT_FAILURE -> safe stop
- UNKNOWN -> safe stop

No recovery decision is allowed to bypass `PermissionManager`.

## Retry and replan limits

The recovery layer keeps bounded counters for:

- retries per objective
- replans per objective
- total plan revisions
- total recovery attempts

When limits are reached, the policy chooses a safe stop instead of looping indefinitely.

## Completed-work preservation

Replanning is required to preserve the completed objective set. A successful prior step must not be replayed simply because a later step failed. The decision payload carries `completed_objectives` and `remaining_objectives` separately so the system can keep the valid prefix of the workflow intact.

## Restart safety

The runtime and recovery layer keep enough state to resume the correct objective after a restart without replaying already-completed work. If persisted state is corrupted or incomplete, the recovery decision should stop safely rather than guessing.

## Duplicate side-effect protection

The recovery layer never re-executes successful side effects as a consequence of failure analysis. It is a planner, not an executor. The runtime and workflow engine continue to be the only places where side effects occur.

## Learning integration

The recovery layer records structured, safe metadata in `LearningMemory` such as:

- failure category
- decision chosen
- retry count
- replan reason
- recovery outcome

It redacts secret-like keys before persisting any learning metadata. The learning history can help future local decision making, but it never bypasses the permission system or safety validations.

## Safety boundaries

This milestone remains local-only and does not:

- create new capabilities
- add cloud logic
- enable remote execution
- add ethical-hacking or offensive security automation
- replace the runtime workflow engine or permission manager

## Validation

The current repo contains a working local replanning implementation and dedicated tests in the `tests/` tree. The active M9 behavior is validated by the local regression suite and by the compile/import checks run in the project environment.
