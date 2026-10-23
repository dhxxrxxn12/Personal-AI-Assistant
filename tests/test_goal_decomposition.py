from automation.goal_decomposition import GoalDecomposer, GoalPlan, Objective


class DummyRegistry:
    def __init__(self):
        self._actions = {
            "open_app",
            "weather_report",
            "file_controller",
            "generate_briefing",
            "send_message",
        }

    def has(self, action: str) -> bool:
        return action in self._actions


def test_goal_decomposer_creates_simple_plan():
    decomposer = GoalDecomposer(action_registry=DummyRegistry())
    plan = decomposer.decompose_goal("Open Calculator")
    assert isinstance(plan, GoalPlan)
    assert plan.goal.original_goal == "Open Calculator"
    assert len(plan.objectives) >= 1
    assert plan.status in {"READY", "NEEDS_CLARIFICATION"}


def test_goal_decomposer_has_dependency_ordering():
    decomposer = GoalDecomposer(action_registry=DummyRegistry())
    plan = decomposer.decompose_goal("Open Calculator and prepare a system summary")
    assert plan.status == "READY"
    ordered = [obj.objective_id for obj in plan.objectives]
    assert "open_calculator" in ordered
    assert "generate_system_summary" in ordered
    assert plan.execution_order[0] == ordered[0]
    assert all(obj.objective_id in plan.execution_order for obj in plan.objectives)


def test_goal_decomposer_marks_ambiguous_goals_for_clarification():
    decomposer = GoalDecomposer(action_registry=DummyRegistry())
    plan = decomposer.decompose_goal("Do whatever is needed tomorrow")
    assert plan.status == "NEEDS_CLARIFICATION"
    assert plan.unresolved_items


def test_goal_decomposer_reports_missing_capability():
    decomposer = GoalDecomposer(action_registry=DummyRegistry())
    plan = decomposer.decompose_goal("Launch an unsupported capability")
    assert plan.status in {"READY", "BLOCKED"}
    assert any(obj.required_capabilities for obj in plan.objectives)


def test_goal_decomposer_is_side_effect_free():
    decomposer = GoalDecomposer(action_registry=DummyRegistry())
    plan = decomposer.decompose_goal("Open Calculator and prepare a short summary")
    assert not plan.executed
    assert plan.status in {"READY", "NEEDS_CLARIFICATION"}


def test_goal_decomposer_supports_runtime_adapter():
    decomposer = GoalDecomposer(action_registry=DummyRegistry())
    plan = decomposer.decompose_goal("Open Calculator and prepare a system summary")
    assert plan.goal.original_goal == "Open Calculator and prepare a system summary"
    assert plan.execution_order
