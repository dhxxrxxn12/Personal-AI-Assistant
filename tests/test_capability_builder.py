"""Regression tests for the local, approval-gated capability builder."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Mapping, Optional, Tuple

import pytest

from automation.autonomous_engine import AutonomousEngine
from automation.permission_manager import PermissionManager
from automation.self_engineering.capability_builder import (
    ApprovalRequired,
    CapabilityBuilder,
    CapabilityDecision,
    CapabilityDefinition,
    CapabilityRegistry,
    CapabilityStatus,
    CapabilityPlanStatus,
)
from automation.self_engineering.capability_store import CapabilityStore
from automation.self_engineering.director import EngineeringDirector
from automation.self_engineering.engineering_planner import EngineeringPlanner
from automation.self_engineering.policies import EngineeringPolicy
from automation.self_engineering.repository_inspector import RepositoryInspector

GOAL = "Build a proactive morning briefing system."

FIXTURE_FILES: Mapping[str, str] = {
    "memory/memory_manager.py": "class MemoryManager:\n    pass\n",
    "core/llm_client.py": "def generate(prompt):\n    return prompt\n",
    "automation/existing_module.py": "VALUE = 1\n",
    "actions/reminder.py": (
        "TOOL = {\n"
        '    "name": "reminder",\n'
        '    "description": "Create and list reminders.",\n'
        "}\n\n"
        "def reminder(parameters):\n"
        '    return "ok"\n'
    ),
    "actions/open_app.py": 'TOOL = {"name": "open_app", "description": "Open a local app."}\n',
    "docs/proactive-roadmap.json": json.dumps({
        "current": [{"capability": "memory"}],
        "missing": [
            {"capability": "scheduler"},
            {"capability": "context aggregator"},
            {"capability": "briefing engine"},
            {"capability": "proactive briefing"},
        ],
    }),
    "tests/test_sample.py": "def test_sample():\n    assert True\n",
}


def fixture_definitions() -> Tuple[CapabilityDefinition, ...]:
    return (
        CapabilityDefinition("memory", "Memory", "Local memory.", ("memory/memory_manager.py",)),
        CapabilityDefinition("reasoning", "Reasoning", "Local reasoning.", ("core/llm_client.py",), risk="medium"),
        CapabilityDefinition(
            "scheduling",
            "Scheduling",
            "Durable scheduling above reminder triggers.",
            ("actions/reminder.py", "scheduler.py"),
            permissions=("recurring schedule consent",),
            risk="medium",
            roadmap_item=True,
            roadmap_aliases=("scheduler",),
        ),
        CapabilityDefinition(
            "calendar_integration",
            "Calendar integration",
            "Read consented calendar summaries.",
            ("integrations/calendar.py",),
            risk="medium",
        ),
        CapabilityDefinition(
            "proactive_execution",
            "Proactive execution",
            "Unprompted prompting with silence gates; durable delivery is absent.",
            ("actions/reminder.py", "automation/scheduler.py"),
            dependencies=("memory",),
            permissions=("proactive prompting consent",),
            risk="medium",
        ),
        CapabilityDefinition(
            "context_aggregator",
            "Context aggregator",
            "Normalize briefing context.",
            ("automation/context_aggregator.py",),
            dependencies=("memory", "calendar_integration"),
            permissions=("per-provider read scopes",),
            risk="high",
            roadmap_item=True,
            roadmap_aliases=("context aggregator",),
        ),
        CapabilityDefinition(
            "briefing_generator",
            "Briefing engine",
            "Compose the briefing.",
            ("automation/briefing.py",),
            dependencies=("context_aggregator", "reasoning"),
            risk="medium",
            roadmap_item=True,
            roadmap_aliases=("briefing engine",),
        ),
        CapabilityDefinition(
            "proactive_briefing",
            "Proactive briefing",
            "End-to-end scheduled briefing.",
            ("automation/proactive_briefing.py",),
            dependencies=("briefing_generator", "scheduling"),
            permissions=("briefing schedule consent",),
            risk="high",
            tests=("duplicate delivery",),
            roadmap_item=True,
            roadmap_aliases=("proactive briefing",),
        ),
    )


def write_repo(root: Path, files: Mapping[str, str] = FIXTURE_FILES) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def store_for(tmp_path: Path) -> CapabilityStore:
    return CapabilityStore(
        tmp_path / "capability_plans.json",
        registry_path=tmp_path / "capability_registry.json",
    )


def builder_for(
    tmp_path: Path,
    *,
    permission_manager: Optional[PermissionManager] = None,
    definitions: Optional[Tuple[CapabilityDefinition, ...]] = None,
    repo_root: Optional[Path] = None,
    store_root: Optional[Path] = None,
) -> CapabilityBuilder:
    repo = Path(repo_root) if repo_root is not None else tmp_path
    permissions = permission_manager or PermissionManager(path=tmp_path / "permissions.json")
    policy = EngineeringPolicy(repo, permissions)
    registry = CapabilityRegistry(
        definitions if definitions is not None else fixture_definitions(),
        permission_manager=permissions,
    )
    store = store_for(Path(store_root) if store_root is not None else tmp_path)
    return CapabilityBuilder(repo, policy=policy, registry=registry, store=store)


def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    write_repo(tmp_path)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "test: capability baseline"], cwd=tmp_path, check=True)
    return tmp_path


# ----------------------------------------------------------------------
# Discovery and registry
# ----------------------------------------------------------------------


def test_repository_discovery_inventories_registered_actions(tmp_path):
    write_repo(tmp_path)
    (tmp_path / "actions" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

    snapshot = RepositoryInspector(tmp_path).inspect()
    specs = CapabilityRegistry(discover_actions=True).discover(snapshot)
    by_id = {item.capability_id: item for item in specs}

    assert "open_app" in by_id
    discovered = by_id["open_app"]
    assert discovered.discovered is True
    assert discovered.status is CapabilityStatus.AVAILABLE
    assert discovered.evidence == ["actions/open_app.py"]
    assert discovered.description == "Open a local app."
    assert discovered.action == "open_app"
    assert discovered.risk == "medium"  # unknown actions require confirmation by default
    assert all(item.capability_id != "helper" for item in specs)


def test_registry_reports_evidence_backed_statuses(tmp_path):
    write_repo(tmp_path)
    snapshot = RepositoryInspector(tmp_path).inspect()
    by_id = {item.capability_id: item for item in CapabilityRegistry(fixture_definitions()).discover(snapshot)}

    assert by_id["memory"].status is CapabilityStatus.AVAILABLE
    assert by_id["reasoning"].status is CapabilityStatus.AVAILABLE
    assert by_id["scheduling"].status is CapabilityStatus.PARTIAL
    assert by_id["scheduling"].missing_evidence == ["scheduler.py"]
    assert by_id["scheduling"].roadmap_reference == "scheduler"
    assert by_id["context_aggregator"].status is CapabilityStatus.PLANNED
    assert by_id["context_aggregator"].roadmap_reference == "context aggregator"
    assert by_id["calendar_integration"].status is CapabilityStatus.MISSING
    assert by_id["proactive_briefing"].status is CapabilityStatus.PLANNED

    payload = by_id["context_aggregator"].to_dict()
    for field in (
        "capability_id",
        "name",
        "description",
        "status",
        "availability",
        "implementation",
        "dependencies",
        "permissions",
        "risk",
        "tests",
        "evidence",
        "missing_evidence",
        "roadmap_reference",
        "discovered",
    ):
        assert field in payload


def test_registry_marks_capabilities_blocked_by_local_permission_policy(tmp_path):
    write_repo(tmp_path)
    permissions = PermissionManager(path=tmp_path / "permissions.json")
    permissions.set_permission("send_message", "blocked")
    definitions = (
        CapabilityDefinition(
            "messaging",
            "Messaging",
            "Send messages.",
            ("actions/open_app.py",),
            action="send_message",
        ),
        CapabilityDefinition("memory", "Memory", "Local memory.", ("memory/memory_manager.py",)),
    )

    snapshot = RepositoryInspector(tmp_path).inspect()
    by_id = {
        item.capability_id: item
        for item in CapabilityRegistry(definitions, permission_manager=permissions).discover(snapshot)
    }

    assert by_id["messaging"].status is CapabilityStatus.BLOCKED
    assert "send_message" in by_id["messaging"].blocked_reason
    assert by_id["memory"].status is CapabilityStatus.AVAILABLE


def test_discovered_action_respects_blocked_permission(tmp_path):
    write_repo(tmp_path)
    permissions = PermissionManager(path=tmp_path / "permissions.json")
    permissions.set_permission("open_app", "blocked")

    snapshot = RepositoryInspector(tmp_path).inspect()
    by_id = {
        item.capability_id: item
        for item in CapabilityRegistry(discover_actions=True, permission_manager=permissions).discover(snapshot)
    }

    assert by_id["open_app"].status is CapabilityStatus.BLOCKED
    assert "blocks" in by_id["open_app"].blocked_reason
    assert by_id["reminder"].status is CapabilityStatus.AVAILABLE


# ----------------------------------------------------------------------
# Dependency graph and gap analysis
# ----------------------------------------------------------------------


def test_dependency_graph_orders_dependencies_first(tmp_path):
    write_repo(tmp_path)
    plan = builder_for(tmp_path).analyze(GOAL)

    graph = plan.dependency_graph
    order = graph.implementation_order
    assert set(graph.nodes) == {
        "memory",
        "reasoning",
        "scheduling",
        "proactive_execution",
        "calendar_integration",
        "context_aggregator",
        "briefing_generator",
        "proactive_briefing",
    }
    assert graph.edges["context_aggregator"] == ["memory", "calendar_integration"]
    assert order.index("memory") < order.index("context_aggregator")
    assert order.index("context_aggregator") < order.index("briefing_generator")
    assert order.index("scheduling") < order.index("proactive_briefing")
    assert order.index("briefing_generator") < order.index("proactive_briefing")
    assert graph.cycles == []
    assert graph.status_of("memory") == CapabilityStatus.AVAILABLE.value
    assert graph.status_of("proactive_briefing") == CapabilityStatus.PLANNED.value
    # Deterministic: analysing twice yields the same order.
    assert builder_for(tmp_path).analyze(GOAL).dependency_graph.implementation_order == order


def test_dependency_cycle_is_reported_as_blocked_without_raising(tmp_path):
    definitions = (
        CapabilityDefinition("alpha", "Alpha", "A.", ("memory/memory_manager.py",), dependencies=("beta",)),
        CapabilityDefinition("beta", "Beta", "B.", ("core/llm_client.py",), dependencies=("alpha",)),
    )
    write_repo(tmp_path)
    plan = builder_for(tmp_path, definitions=definitions).analyze("Build the alpha beta capability", roots=("alpha",))

    graph = plan.dependency_graph
    assert graph.cycles
    assert set(graph.blocked) >= {"alpha", "beta"}
    assert all("cycle" in item.blocked_reason for item in plan.blocked_capabilities if item.capability_id in {"alpha", "beta"})
    assert plan.roadmap_targets() == []


def test_unknown_dependency_becomes_a_blocked_placeholder(tmp_path):
    definitions = (
        CapabilityDefinition("alpha", "Alpha", "A.", ("memory/memory_manager.py",), dependencies=("ghost",)),
    )
    write_repo(tmp_path)
    plan = builder_for(tmp_path, definitions=definitions).analyze("Build the alpha capability", roots=("alpha",))

    assert "ghost" in plan.dependency_graph.nodes
    assert plan.dependency_graph.status_of("ghost") == CapabilityStatus.BLOCKED.value
    placeholders = [item for item in plan.blocked_capabilities if item.capability_id == "ghost"]
    assert placeholders and "undefined capability" in placeholders[0].blocked_reason
    assert "ghost" in {item.capability_id for item in plan.registry}


def test_gap_analysis_separates_existing_partial_missing_and_planned(tmp_path):
    write_repo(tmp_path)
    plan = builder_for(tmp_path).analyze(GOAL)

    existing = {item.capability_id for item in plan.current_capabilities}
    partial = {item.capability_id for item in plan.partial_capabilities}
    missing = {item.capability_id for item in plan.missing_capabilities}
    planned = {item.capability_id for item in plan.planned_capabilities}

    assert {"memory", "reasoning"} <= existing
    assert partial == {"scheduling", "proactive_execution"}
    assert {"proactive_briefing", "briefing_generator", "context_aggregator", "calendar_integration"} <= missing
    assert planned == {"proactive_briefing", "briefing_generator", "context_aggregator"}
    assert all(item.status is not CapabilityStatus.AVAILABLE for item in plan.missing_capabilities)
    assert "recurring schedule consent" in plan.required_permissions
    assert "high risk capability: Context aggregator" in plan.risks
    assert [layer["layer"] for layer in plan.proposed_architecture] == ["trigger", "context", "generation"]
    assert plan.repository_snapshot["action_count"] == 2
    assert plan.repository_snapshot["test_count"] == 1


def test_roadmap_phases_follow_dependency_levels_and_exclude_available(tmp_path):
    write_repo(tmp_path)
    plan = builder_for(tmp_path).analyze(GOAL)

    phases = plan.estimated_phases
    scheduled = [item for item in phases if "scheduling" in item["capabilities"]]
    aggregated = [item for item in phases if "context_aggregator" in item["capabilities"]]
    end_to_end = [item for item in phases if "proactive_briefing" in item["capabilities"]]

    assert scheduled and aggregated and end_to_end
    assert scheduled[0]["phase"] < end_to_end[0]["phase"]
    assert aggregated[0]["phase"] < end_to_end[0]["phase"]
    assert all(item["gate"] for item in phases)
    assert not any(item.capability_id in {"memory", "reasoning"} for item in plan.roadmap_targets())


# ----------------------------------------------------------------------
# Approval gate
# ----------------------------------------------------------------------


def test_analysis_is_read_only_and_stops_at_the_approval_gate(tmp_path):
    repo = write_repo(tmp_path / "repo")
    before = sorted(path.as_posix() for path in repo.rglob("*") if path.is_file())
    builder = builder_for(tmp_path, repo_root=repo, store_root=tmp_path)

    plan = builder.analyze(GOAL)

    after = sorted(path.as_posix() for path in repo.rglob("*") if path.is_file())
    assert plan.status == CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value
    assert plan.is_awaiting_approval and not plan.is_approved and not plan.is_cancelled
    assert plan.approval_required is True
    assert plan.revision == 1
    assert after == before  # no source or file change happens before approval
    stages = [event["stage"] for event in plan.events]
    assert stages[-1] == "capability_approval_required"
    assert "engineering_handoff" not in stages
    assert "capability_approved" not in stages


def test_engineering_handoff_requires_explicit_approval(tmp_path):
    write_repo(tmp_path)
    builder = builder_for(tmp_path)
    plan = builder.analyze(GOAL)

    with pytest.raises(ApprovalRequired, match="explicit approval"):
        builder.execute(plan, director=EngineeringDirector(tmp_path, enabled=True))

    approved = builder.approve(plan)
    with pytest.raises(RuntimeError, match="disabled"):
        builder.execute(approved, director=EngineeringDirector(tmp_path, enabled=False))
    with pytest.raises(ValueError, match="EngineeringDirector"):
        builder.execute(approved, director=None)


def test_approve_modify_and_cancel_record_the_operator_decision(tmp_path):
    write_repo(tmp_path)
    builder = builder_for(tmp_path)
    plan = builder.analyze(GOAL)

    approved = builder.approve(plan, approved_by="tester")
    assert approved.status == CapabilityPlanStatus.APPROVED.value
    assert approved.approved_by == "tester" and approved.approved_at
    assert approved.approval_required is False
    assert builder.approve(approved) is approved  # approving twice is idempotent

    revised = builder.modify(
        approved,
        note="defer calendar work",
        defer_capabilities=["calendar_integration"],
        add_permissions=["manual calendar entry"],
    )
    assert revised.revision == 2
    assert revised.is_awaiting_approval
    assert revised.approved_at is None and revised.approved_by is None
    assert revised.deferred_capabilities == ["calendar_integration"]
    assert "manual calendar entry" in revised.required_permissions
    assert revised.modifications[0]["note"] == "defer calendar work"
    assert not any(item.capability_id == "calendar_integration" for item in revised.roadmap_targets())
    assert all("calendar_integration" not in phase["capabilities"] for phase in revised.estimated_phases)

    cancelled = builder.cancel(revised, reason="not this week")
    assert cancelled.status == CapabilityPlanStatus.CANCELLED.value
    assert cancelled.cancelled_reason == "not this week"
    assert cancelled.approval_required is False
    with pytest.raises(ValueError, match="cancelled"):
        builder.approve(cancelled)
    with pytest.raises(ValueError, match="cancelled"):
        builder.modify(cancelled)
    with pytest.raises(ApprovalRequired, match="cancelled"):
        builder.execute(cancelled, director=EngineeringDirector(tmp_path, enabled=True))


def test_submit_decision_maps_approve_modify_and_cancel(tmp_path):
    write_repo(tmp_path)
    builder = builder_for(tmp_path)

    assert builder.submit_decision(builder.analyze(GOAL), "approve").is_approved
    assert builder.submit_decision(builder.analyze(GOAL), CapabilityDecision.MODIFY).revision == 2
    assert builder.submit_decision(builder.analyze(GOAL), "cancel", reason="later").is_cancelled
    with pytest.raises(ValueError, match="approve, modify, or cancel"):
        builder.submit_decision(builder.analyze(GOAL), "escalate")


# ----------------------------------------------------------------------
# Invalid input, permission enforcement, and persistence
# ----------------------------------------------------------------------


def test_invalid_requests_are_rejected(tmp_path):
    write_repo(tmp_path)
    builder = builder_for(tmp_path)

    with pytest.raises(ValueError, match="requires a goal"):
        builder.analyze("   ")

    plan = builder.analyze(GOAL)
    with pytest.raises(ValueError, match="Unknown capability ids"):
        builder.modify(plan, defer_capabilities=["not_a_capability"])


def test_permission_enforcement_blocks_repository_inspection(tmp_path):
    write_repo(tmp_path)
    permissions = PermissionManager(path=tmp_path / "permissions.json")
    permissions.set_permission("self_engineering", "blocked")
    builder = builder_for(tmp_path, permission_manager=permissions)

    with pytest.raises(PermissionError):
        builder.analyze(GOAL)


def test_capability_state_survives_a_restart(tmp_path):
    write_repo(tmp_path)
    plan = builder_for(tmp_path).approve(builder_for(tmp_path).analyze(GOAL), approved_by="tester")

    restarted = builder_for(tmp_path)  # new builder and store instance, same local files
    reloaded = restarted.load(plan.analysis_id)

    assert reloaded is not None
    assert reloaded.goal == plan.goal
    assert reloaded.status == CapabilityPlanStatus.APPROVED.value
    assert reloaded.approved_by == "tester"
    assert reloaded.revision == plan.revision
    assert [item.capability_id for item in reloaded.missing_capabilities] == [
        item.capability_id for item in plan.missing_capabilities
    ]
    assert reloaded.required_permissions == plan.required_permissions
    assert reloaded.dependency_graph.implementation_order == plan.dependency_graph.implementation_order
    assert restarted.latest().analysis_id == plan.analysis_id

    store = store_for(tmp_path)
    summaries = store.plan_summaries()
    assert summaries[-1]["analysis_id"] == plan.analysis_id
    registry = store.registry()
    assert registry["count"] >= len(plan.registry)
    assert {item["capability_id"] for item in registry["capabilities"]} >= {"memory", "proactive_briefing"}


def test_persistence_redacts_secrets_and_tolerates_a_corrupt_store(tmp_path):
    write_repo(tmp_path)
    builder = builder_for(tmp_path)

    builder.analyze("Build a briefing helper for token=super-secret-value")
    stored = builder.store.path.read_text(encoding="utf-8")
    assert "super-secret-value" not in stored
    assert "<redacted>" in stored

    builder.store.path.write_text("{not json", encoding="utf-8")
    assert builder.store.plans() == []
    assert builder.store.latest_plan() is None
    assert builder.analyze(GOAL).status == CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value


# ----------------------------------------------------------------------
# Self-engineering integration
# ----------------------------------------------------------------------


def test_roadmap_becomes_a_valid_local_engineering_plan(tmp_path):
    write_repo(tmp_path)
    builder = builder_for(tmp_path)
    plan = builder.analyze(GOAL)
    snapshot = builder.inspector.inspect()

    candidate = builder.engineering_candidate(plan, snapshot)
    validated = EngineeringPlanner().validate(candidate, source="capability_roadmap", snapshot=snapshot)

    targets = [item.capability_id for item in plan.roadmap_targets()]
    assert [step.id for step in validated.steps] == [f"implement-{item}" for item in targets]
    assert all(step.operation == "create_file" for step in validated.steps)
    assert all(step.requires_approval for step in validated.steps)
    step_ids = {step.id for step in validated.steps}
    assert all(dependency in step_ids for step in validated.steps for dependency in step.dependencies)
    assert validated.required_approvals
    assert validated.risk in {"medium", "high", "critical"}
    assert "tests/test_sample.py" in validated.tests
    assert all(
        path.startswith("automation/") or path.startswith("tests/")
        for path in validated.affected_files
    )


def test_roadmap_verification_tests_are_visible_in_the_committed_sandbox(tmp_path):
    git_repo(tmp_path)
    (tmp_path / "tests" / "test_untracked.py").write_text("def test_new():\n    assert True\n", encoding="utf-8")
    builder = builder_for(tmp_path)
    plan = builder.analyze(GOAL)
    snapshot = builder.inspector.inspect()

    candidate = builder.engineering_candidate(plan, snapshot)

    assert "tests/test_untracked.py" in snapshot.tests  # the working tree sees it
    assert "tests/test_untracked.py" not in candidate["tests"]  # the sandbox would not
    assert "tests/test_sample.py" in candidate["tests"]


def test_approved_roadmap_runs_through_the_local_self_engineering_director(tmp_path):
    git_repo(tmp_path)
    builder = builder_for(tmp_path)
    plan = builder.approve(builder.analyze(GOAL), approved_by="tester")
    target = plan.roadmap_targets()[0]
    proposed = f"automation/{target.capability_id}.py"
    baseline = (tmp_path / "tests" / "test_sample.py").read_text(encoding="utf-8")

    def implementation(executor, validated_plan):
        # The trusted application boundary implements exactly the validated plan.
        for step in validated_plan.steps:
            for path in step.files:
                executor.create_file(path, f"# placeholder for {step.id}\n", approved=True)

    task = builder.execute(
        plan,
        director=EngineeringDirector(tmp_path, enabled=True),
        implementation=implementation,
    )

    assert task.status in {"completed", "awaiting_deploy_approval"}
    assert task.repository_inspection and task.capability_analysis
    assert plan.engineering_task_id == task.task_id
    assert any(event["stage"] == "engineering_handoff" for event in plan.events)
    assert any(event["stage"] == "engineering_task_created" for event in plan.events)
    assert not (tmp_path / proposed).exists()  # production stays untouched
    assert (tmp_path / "tests" / "test_sample.py").read_text(encoding="utf-8") == baseline


def test_autonomous_engine_routes_capability_goals_locally(tmp_path):
    write_repo(tmp_path)
    engine = AutonomousEngine(action_registry=None, logger=lambda message: None)
    engine.attach_capability_builder(builder_for(tmp_path))

    assert engine.is_capability_goal("Show me a capability roadmap for the briefing")
    assert engine.is_capability_goal("run a gap analysis")
    assert not engine.is_capability_goal("play some music")

    plan = engine.analyze_capability_goal(GOAL)
    assert plan.status == CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value

    detached = AutonomousEngine(action_registry=None, logger=lambda message: None)
    with pytest.raises(RuntimeError, match="capability builder"):
        detached.analyze_capability_goal(GOAL)
