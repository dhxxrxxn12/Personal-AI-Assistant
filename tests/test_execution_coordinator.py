from automation.execution_coordinator import ExecutionCoordinator, ExecutionMode


class DummyRegistry:
    def __init__(self, safe_actions=None, blocked_actions=None):
        self.safe_actions = set(safe_actions or {"read_status", "weather_report", "open_app"})
        self.blocked_actions = set(blocked_actions or {"send_message"})

    def has(self, name):
        return name in self.safe_actions or name not in self.blocked_actions

    def names(self):
        return sorted(self.safe_actions | self.blocked_actions)


class DummyPermissionManager:
    def __init__(self):
        self.allow = set()
        self.blocked = {"send_message"}

    def check(self, action, parameters=None):
        if action in self.blocked:
            return {"allowed": False, "requires_confirmation": True, "reason": "requires confirmation"}
        return {"allowed": True, "requires_confirmation": False, "reason": "ok"}


class DummyWorkflow:
    def __init__(self, steps):
        self.steps = steps
        self.status = "PLANNED"
        self.current_step = None


class DummyStep:
    def __init__(self, step_id, action_id, dependencies=None, status="PENDING"):
        self.step_id = step_id
        self.action_id = action_id
        self.dependencies = set(dependencies or [])
        self.status = status


def test_sequential_mode_remains_default():
    coordinator = ExecutionCoordinator()
    result = coordinator.coordinate(
        objective_ids=["a", "b"],
        completed=[],
        dependencies={"a": [], "b": ["a"]},
    )
    assert result.execution_mode == ExecutionMode.SEQUENTIAL
    assert result.ready_objectives == ["a"]
    assert result.next_objective == "a"


def test_independent_objectives_can_be_identified():
    coordinator = ExecutionCoordinator()
    result = coordinator.coordinate(
        objective_ids=["a", "b", "c"],
        completed=[],
        dependencies={"a": [], "b": [], "c": ["a"]},
    )
    assert result.ready_objectives == ["a", "b"]
    assert "c" in result.waiting_objectives


def test_permission_gates_protected_objective():
    coordinator = ExecutionCoordinator(action_registry=DummyRegistry(), permissions=DummyPermissionManager())
    result = coordinator.coordinate(
        objective_ids=["safe", "protected"],
        completed=[],
        dependencies={"safe": [], "protected": []},
        action_map={"safe": "read_status", "protected": "send_message"},
    )
    assert "protected" in result.permission_waiting
    assert result.execution_mode in {ExecutionMode.SEQUENTIAL, ExecutionMode.PARALLEL_SAFE}


def test_completed_objectives_are_not_replayed():
    coordinator = ExecutionCoordinator()
    result = coordinator.coordinate(
        objective_ids=["a", "b"],
        completed=["a"],
        dependencies={"a": [], "b": ["a"]},
    )
    assert result.completed_objectives == ["a"]
    assert "a" not in result.ready_objectives
    assert result.next_objective == "b"


def test_unknown_dependency_blocks_plan():
    coordinator = ExecutionCoordinator()
    result = coordinator.coordinate(
        objective_ids=["a", "b"],
        completed=[],
        dependencies={"a": ["missing"]},
    )
    assert result.blocked_objectives == ["a"]
    assert result.status == "BLOCKED"
