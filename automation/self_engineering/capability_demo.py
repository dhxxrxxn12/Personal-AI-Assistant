"""Local command-line demonstration of the capability builder.

Everything here runs on the local machine.  The default action is a strictly
read-only analysis that stops at the approval gate:

    PYTHONPATH=. .venv/bin/python -m automation.self_engineering.capability_demo \\
        "Build a proactive JARVIS morning briefing system."

``--approve`` and ``--cancel`` only change the recorded approval state.  A
``--dry-run-handoff`` additionally requires ``JARVIS_SELF_ENGINEERING_ENABLED=1``
and an approved plan; it then hands the roadmap to the existing local
self-engineering Director, which creates an isolated Git worktree, writes
placeholder modules for the approved steps only, runs the tests, verifies the
result, and stops before any deployment.  Production is never modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from .capability_builder import CapabilityBuilder, CapabilityPlan
from .capability_store import CapabilityStore
from .director import EngineeringDirector

DRY_RUN_NOTE = (
    "placeholder modules only: the real capability implementation stays behind the "
    "trusted application boundary, so the morning briefing itself is not built here"
)


def _section(title: str) -> List[str]:
    return ["", title, "-" * len(title)]


def _status_line(spec: Any) -> str:
    evidence = ", ".join(spec.evidence) if spec.evidence else "no local evidence"
    suffix = ""
    if spec.blocked_reason:
        suffix = f" ({spec.blocked_reason})"
    elif spec.roadmap_reference:
        suffix = f" (local roadmap: {spec.roadmap_reference})"
    return f"  {spec.status.value.upper():<9} {spec.capability_id:<28} {spec.name}{suffix}\n            evidence: {evidence}"


def render(plan: CapabilityPlan) -> str:
    """Render the read-only capability report for a terminal."""
    lines: List[str] = ["INSPECT"]
    snapshot = plan.repository_snapshot
    lines.append(f"  repository    : {snapshot.get('root')}")
    lines.append(f"  files inspected: {snapshot.get('file_count')} | tests: {snapshot.get('test_count')} | local actions: {snapshot.get('action_count')}")
    lines.append(f"  git status len: {len(snapshot.get('git_status') or [])} entry(ies); truncated: {snapshot.get('truncated')}")

    lines += _section(f"INVENTORY ({len(plan.registry)} capabilities, {sum(1 for item in plan.registry if item.discovered)} discovered from the repository)")
    for spec in plan.registry:
        lines.append(_status_line(spec))

    lines += _section("GAP ANALYSIS")
    lines.append("EXISTING")
    lines.extend(f"  {item.capability_id}" for item in plan.current_capabilities)
    lines.append("PARTIAL")
    lines.extend(f"  {item.capability_id}" for item in plan.partial_capabilities)
    lines.append("MISSING / PLANNED (needs engineering)")
    lines.extend(
        f"  {item.status.value.upper():<8} {item.capability_id}"
        for item in plan.missing_capabilities
    )
    lines.append("BLOCKED")
    lines.extend(f"  {item.capability_id}: {item.blocked_reason}" for item in plan.blocked_capabilities)

    lines += _section("DEPENDENCY GRAPH")
    for capability_id in plan.dependency_graph.nodes:
        dependencies = plan.dependency_graph.edges.get(capability_id) or []
        status = plan.dependency_graph.status_of(capability_id)
        lines.append(f"  {capability_id} [{status}] -> {', '.join(dependencies) or 'no dependencies'}")
    lines.append(f"  implementation order: {' -> '.join(plan.dependency_graph.implementation_order)}")
    lines.append(f"  cycles: {plan.dependency_graph.cycles or 'none'}")

    lines += _section("ENGINEERING ROADMAP")
    for phase in plan.estimated_phases:
        lines.append(f"  phase {phase['phase']} (level {phase['level']}): {', '.join(phase['capabilities'])}")
        lines.append(f"      gate  : {phase['gate']}")
        lines.append(f"      risk  : {phase['risk']}")
    lines.append(f"  permissions : {', '.join(plan.required_permissions) or 'none'}")
    lines.append(f"  integrations: {', '.join(plan.required_integrations) or 'none'}")
    lines.append(f"  risks       : {', '.join(plan.risks) or 'none'}")
    lines.append(f"  architecture: {', '.join(layer['layer'] for layer in plan.proposed_architecture)}")

    lines += _section("APPROVAL GATE")
    lines.append(f"  analysis id : {plan.analysis_id}")
    lines.append(f"  revision    : {plan.revision}")
    lines.append(f"  status      : {plan.status}")
    lines.append(f"  approval    : {'required' if plan.approval_required else 'recorded'}")
    lines.append(f"  next        : APPROVE | MODIFY | CANCEL")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local JARVIS capability analysis (no Cloud required).")
    parser.add_argument("goal", nargs="?", default="Build a proactive JARVIS morning briefing system.")
    parser.add_argument("--root", default=".", help="Repository root to inspect (default: current directory).")
    parser.add_argument("--json", action="store_true", help="Print the structured plan instead of the report.")
    parser.add_argument("--analysis-id", default="", help="Use an existing persisted plan instead of analysing again.")
    parser.add_argument("--approve", action="store_true", help="Record explicit approval of the plan.")
    parser.add_argument("--defer", action="append", default=[], help="Capability id to defer (MODIFY).")
    parser.add_argument("--cancel", default="", help="Cancel the plan with an optional reason.")
    parser.add_argument("--list", action="store_true", help="List persisted capability plans.")
    parser.add_argument("--dry-run-handoff", action="store_true", help="Run an approved plan through the local sandbox with placeholder modules.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the local demonstration and return a process exit code."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    store = CapabilityStore()
    builder = CapabilityBuilder(root, store=store)

    if args.list:
        for summary in store.plan_summaries():
            print(
                f"{summary['analysis_id']}  {summary['status']:<32} rev {summary['revision']}  "
                f"{summary['goal']}"
            )
        return 0

    if args.analysis_id:
        plan = store.load_plan(args.analysis_id)
        if plan is None:
            print(f"No persisted capability plan with id '{args.analysis_id}'.", file=sys.stderr)
            return 2
    else:
        plan = builder.analyze(args.goal)

    if args.cancel:
        plan = builder.cancel(plan, reason=args.cancel)
        print(f"Capability plan {plan.analysis_id} cancelled: {plan.cancelled_reason or 'no reason given'}")
        return 0

    if args.defer:
        plan = builder.modify(plan, note="operator deferred capabilities", defer_capabilities=args.defer)
        print(f"Capability plan {plan.analysis_id} revised to revision {plan.revision} (awaiting approval).")

    if args.approve:
        plan = builder.approve(plan, approved_by="local_cli")
        print(f"Capability plan {plan.analysis_id} approved by {plan.approved_by}.")

    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    print(render(plan))

    if not args.dry_run_handoff:
        return 0

    director = EngineeringDirector(root)
    if not director.enabled:
        print(
            "\nSelf-engineering is disabled; set JARVIS_SELF_ENGINEERING_ENABLED=1 "
            "to run this approved roadmap in the local sandbox.",
            file=sys.stderr,
        )
        return 3

    def placeholder_implementation(executor: Any, validated_plan: Any) -> None:
        """Trusted boundary placeholder: exercise the lifecycle, write no real code."""
        for step in validated_plan.steps:
            for path in step.files:
                executor.create_file(
                    path,
                    "# placeholder created by the capability dry-run\n",
                    approved=True,
                )

    task = builder.execute(plan, director=director, implementation=placeholder_implementation)
    print("\nLOCAL ENGINEERING HANDOFF (dry run)")
    print(f"  {DRY_RUN_NOTE}")
    print(f"  task id   : {task.task_id}")
    print(f"  status    : {task.status}")
    print(f"  production: {task.production_impact}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual local demonstration
    raise SystemExit(main())
