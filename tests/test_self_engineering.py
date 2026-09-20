"""Regression tests for the opt-in self-engineering foundation."""

from __future__ import annotations

import subprocess
import sys
import hashlib
from pathlib import Path

import pytest

from automation.permission_manager import PermissionManager
from automation.self_engineering.capability_analyzer import CapabilityAnalyzer
from automation.self_engineering.change_memory import ChangeMemory, EngineeringMemoryRecord
from automation.self_engineering.code_executor import CodeExecutor
from automation.self_engineering.director import EngineeringDirector
from automation.self_engineering.engineering_planner import EngineeringPlanner, PlanValidationError
from automation.self_engineering.failure_analyzer import FailureAnalyzer
from automation.self_engineering.git_manager import GitManager
from automation.self_engineering.models import CapabilityAnalysis, RepositorySnapshot
from automation.self_engineering.policies import ApprovalRequired, EngineeringOperation, EngineeringPolicy
from automation.self_engineering.repair_engine import RepairEngine
from automation.self_engineering.repository_inspector import RepositoryInspector
from automation.self_engineering.test_runner import TestResult


def policy_for(path: Path) -> EngineeringPolicy:
    return EngineeringPolicy(path, PermissionManager(path=path / "permissions.json"))


def test_repository_inspection_is_structured_and_bounded(tmp_path):
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=never-read\n", encoding="utf-8")

    snapshot = RepositoryInspector(tmp_path, max_entries=10).inspect()

    assert "app.py" in snapshot.tree
    assert "tests/test_app.py" in snapshot.tests
    assert snapshot.python_modules[0].path == "app.py"
    assert "fastapi" in snapshot.python_modules[0].imports
    assert snapshot.to_dict()["files"]


def test_capability_analysis_identifies_goal_specific_gaps():
    snapshot = RepositorySnapshot(
        root="/tmp/repo",
        generated_at="now",
        tree=[
            "automation/autonomous_planner.py",
            "automation/permission_manager.py",
            "automation/learning_memory.py",
            "cloud/voice.py",
            "actions/reminder.py",
            "tests/test_one.py",
            "Dockerfile",
        ],
        tests=["tests/test_one.py"],
    )
    analysis = CapabilityAnalyzer().analyze("Build proactive morning intelligence", snapshot)

    assert analysis.current_capabilities["voice interaction"] is True
    assert "proactive scheduler" in analysis.missing_capabilities
    assert "personal data aggregation" in analysis.risks


def test_invalid_plan_is_rejected_and_fallback_is_safe():
    planner = EngineeringPlanner()
    with pytest.raises(PlanValidationError, match="no tasks"):
        planner.validate({"goal": "x", "steps": [], "verification_plan": ["tests"]})

    fallback = planner.fallback(
        "Build proactive morning intelligence",
        CapabilityAnalysis("goal", {}, ["proactive scheduler"], ["scheduler"], [], {}),
        RepositorySnapshot("/tmp/repo", "now", tests=["tests/test_one.py"]),
    )
    assert fallback.source == "deterministic_fallback"
    assert all(step.operation in {"inspect_repository", "run_test"} for step in fallback.steps)
    assert all(step.risk == "low" for step in fallback.steps)


def test_policy_requires_approval_and_protects_secrets(tmp_path):
    policy = policy_for(tmp_path)
    with pytest.raises(ApprovalRequired):
        policy.require(EngineeringOperation.CREATE_FILE, "new.py")
    with pytest.raises(PermissionError, match="protected"):
        policy.require(EngineeringOperation.MODIFY_FILE, ".env", approved=True)
    policy.require(EngineeringOperation.RUN_TEST)


def test_code_executor_produces_diff_and_blocks_traversal(tmp_path):
    policy = policy_for(tmp_path)
    executor = CodeExecutor(policy)
    with pytest.raises(ApprovalRequired):
        executor.create_file("new.py", "value = 1\n")

    result = executor.create_file("new.py", "value = 1\n", approved=True)
    assert "new.py" in result.diff
    changed = executor.modify_file("new.py", "value = 2\n", expected_sha256=result.after_sha256, approved=True)
    assert "-value = 1" in changed.diff
    with pytest.raises(PermissionError, match="outside"):
        executor.create_file("../outside.py", "x = 1\n", approved=True)


def test_repair_engine_stops_at_configured_attempt_limit():
    calls = {"test": 0, "repair": 0}

    def test():
        calls["test"] += 1
        return TestResult(["test"], 1, "FAILED test_app.py::test_ok", "", 0.0)

    def repair(_analysis, _attempt):
        calls["repair"] += 1
        return True

    report = RepairEngine(max_attempts=2).run(test, repair)
    assert report.success is False
    assert report.stopped_reason.startswith("maximum repair attempts")
    assert calls == {"test": 3, "repair": 2}


def test_failure_analyzer_redacts_secrets():
    result = TestResult(["pytest"], 1, "api_key=super-secret AssertionError in test_app.py:4", "", 0.0)
    analysis = FailureAnalyzer().analyze(result)
    assert analysis.category == "test_failure"
    assert "super-secret" not in analysis.relevant_output
    assert "test_app.py" in analysis.likely_files


def test_git_checkpoint_and_rollback_are_explicit(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    policy = policy_for(tmp_path)
    executor = CodeExecutor(policy)
    git = GitManager(tmp_path, policy)
    first = git.checkpoint("test: initial", ["tracked.py"], approved=True)
    current_hash = hashlib.sha256(executor.read_source("tracked.py").encode("utf-8")).hexdigest()
    executor.modify_file("tracked.py", "value = 2\n", expected_sha256=current_hash, approved=True)
    second = git.checkpoint("test: change", ["tracked.py"], approved=True)
    assert first != second
    git.rollback(second, approved=True)
    assert (tmp_path / "tracked.py").read_text(encoding="utf-8") == "value = 1\n"


def test_change_memory_requires_exact_context(tmp_path):
    memory = ChangeMemory(tmp_path / "engineering.json")
    memory.record(EngineeringMemoryRecord(
        goal="Build proactive morning intelligence",
        capabilities=["proactive scheduler"],
        subsystem="automation",
        actions=["create_file"],
    ))
    assert len(memory.find_similar(
        goal="Build proactive morning intelligence",
        capability="proactive scheduler",
        subsystem="automation",
        action="create_file",
    )) == 1
    assert memory.find_similar(
        goal="Build proactive evening intelligence",
        capability="proactive scheduler",
        subsystem="automation",
    ) == []


def test_director_rejects_provider_plan_and_emits_trace(tmp_path):
    memory = ChangeMemory(tmp_path / "engineering.json")
    director = EngineeringDirector(tmp_path, enabled=True, memory=memory)
    run = director.prepare(
        "Build proactive morning intelligence",
        candidate_plan={"goal": "wrong", "steps": [], "verification_plan": []},
    )
    stages = [event.stage for event in run.events]
    assert "PLAN_REJECTED" in stages
    assert run.plan is not None
    assert run.plan.source == "deterministic_fallback"
    assert director.report(run)["snapshot"] is not None
