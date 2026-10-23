# Goal Decomposition

## Purpose

The goal decomposition layer sits above the existing local runtime and turns a natural-language goal into a deterministic, dependency-aware plan without executing actions during planning.

## Architecture

User request
  -> GoalDecomposer
  -> GoalPlan / Objective list
  -> AutonomousRuntime
  -> WorkflowEngine
  -> PermissionManager
  -> ActionRegistry
  -> Verification and learning

## Data model

- Goal: original goal, normalized goal, metadata
- Objective: objective id, description, purpose, dependencies, required capabilities, risk, priority, status
- GoalPlan: plan metadata, objectives, dependency graph, execution order, confidence, unresolved items

## Dependency behavior

- objectives are ordered deterministically
- dependencies are validated against existing objective ids
- cycles are rejected as invalid
- independent objectives remain parallelizable at the plan level, even though execution is still routed through the existing workflow engine

## Capability awareness

Each objective tracks the required capability. If the action registry does not know the capability, the objective is marked as blocked or missing capability without executing anything.

## Confidence and clarification

- clear goals are HIGH confidence
- ambiguous goals such as "do whatever is needed tomorrow" are LOW confidence and require clarification
- the decomposer never invents missing information

## Risk classification

Risk is mapped to the existing permission concepts at the planning layer:

- READ_ONLY
- LOW_RISK
- CONFIRMATION_REQUIRED
- BLOCKED

Permission enforcement remains downstream in the runtime and workflow engine.

## Example

User:
"Open Calculator and prepare a system summary."

Decomposed plan:

1. open_calculator
2. collect_system_information
3. generate_system_summary

Dependencies:
- collect_system_information -> generate_system_summary

## Side-effect safety

The decomposition engine is read-only. It never opens apps, sends messages, modifies files, runs shell commands, or calls external APIs.

## Persistence

If a caller chooses to persist a plan, it should use the local project conventions and keep only local, non-secret metadata.

## Integration

The runtime façade can optionally call the decomposer before normal execution, but it does not replace the existing planner, workflow engine, permission manager, or learning memory.
