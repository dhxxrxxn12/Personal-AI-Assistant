"""Local proactive decision and execution engine.

This module sits between normalized local context and the existing action system.
It does not replace the action registry, permission manager, or learning memory;
it orchestrates them and keeps all decisions deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .learning_memory import LearningMemory
from .permission_manager import PermissionManager


class DecisionState(str, Enum):
    NO_ACTION = "NO_ACTION"
    INFORM = "INFORM"
    PREPARE = "PREPARE"
    ASK_PERMISSION = "ASK_PERMISSION"
    EXECUTE = "EXECUTE"
    BLOCKED = "BLOCKED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Situation:
    category: str
    summary: str
    trigger: str
    severity: str = "medium"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def normalize_item(item: Any) -> Dict[str, Any]:
        if hasattr(item, "to_dict"):
            item = item.to_dict()
        if isinstance(item, Mapping):
            payload = dict(item)
            return {
                "source": str(payload.get("source") or payload.get("provider") or "context"),
                "category": str(payload.get("category") or payload.get("source") or "context"),
                "title": str(payload.get("title") or payload.get("label") or payload.get("name") or "context"),
                "content": str(payload.get("content") or payload.get("summary") or payload.get("weather") or payload.get("workflow") or payload.get("reminder") or payload.get("text") or ""),
                "priority": str(payload.get("priority") or "medium"),
            }
        return {"source": "context", "category": "context", "title": str(item), "content": str(item), "priority": "medium"}

    @classmethod
    def from_item(cls, item: Any) -> "Situation":
        payload = cls.normalize_item(item)
        title = str(payload.get("title") or payload.get("name") or payload.get("summary") or "").strip()
        content = str(payload.get("content") or "").strip()
        text = (f"{title} {content}").strip()
        lowered = text.lower()

        if "submit assignment" in lowered or "assignment" in lowered or "deadline" in lowered or "in 15 minutes" in lowered or "reminder" in lowered:
            summary = title or "Important reminder approaching."
            return cls("reminder", f"Important reminder approaching: {summary}.", "reminder_approaching", "high", {"item": payload})
        if "disk" in lowered or "storage" in lowered or "cpu" in lowered or "memory" in lowered or ("system" in lowered and "alert" in lowered):
            summary = title or "System resource pressure requires attention."
            return cls("system", f"System resource pressure requires attention: {summary}.", "system_alert", "high", {"item": payload})
        if "open" in lowered and ("calculator" in lowered or "app" in lowered or "browser" in lowered or "chrome" in lowered):
            summary = title or "Open requested app or tool."
            return cls("action", f"Open requested app or tool: {summary}.", "open_request", "medium", {"item": payload})
        if "send" in lowered and ("message" in lowered or "email" in lowered):
            summary = title or "Message delivery needs approval."
            return cls("communication", f"Message delivery needs approval: {summary}.", "message_request", "high", {"item": payload})
        if "delete" in lowered or "remove" in lowered or "destroy" in lowered:
            summary = title or "Destructive action requested."
            return cls("destructive", f"Destructive action requested: {summary}.", "destructive_request", "critical", {"item": payload})
        if "task" in lowered or "workflow" in lowered or "assignment" in lowered or "overdue" in lowered:
            summary = title or "Task is overdue or requires attention."
            return cls("task", f"Task requires attention: {summary}.", "task_overdue", "medium", {"item": payload})
        if "weather" in lowered:
            summary = title or "Weather context is available."
            return cls("weather", f"Weather context is available: {summary}.", "weather_report", "low", {"item": payload})
        if "important" in lowered or "urgent" in lowered:
            summary = title or "High-priority local context is present."
            return cls("priority", f"High-priority local context is present: {summary}.", "priority_signal", "high", {"item": payload})
        summary = title or "Relevant local context is present."
        return cls("general", f"Relevant local context is present: {summary}.", "general_context", "medium", {"item": payload})


@dataclass
class Decision:
    state: DecisionState
    situation: Optional[Situation]
    risk: RiskLevel
    reason: str
    action: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires_user_approval: bool = False
    dedupe_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "risk": self.risk.value,
            "action": self.action,
            "parameters": dict(self.parameters),
            "reason": self.reason,
            "requires_user_approval": self.requires_user_approval,
            "dedupe_key": self.dedupe_key,
            "situation": self.situation.summary if self.situation else "",
        }


@dataclass
class ActionResult:
    action_id: str
    status: str
    started_at: str
    completed_at: str
    output: str = ""
    error: str = ""
    verification_required: bool = False
    verification_passed: Optional[bool] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "output": self.output,
            "error": self.error,
            "verification_required": self.verification_required,
            "verification_passed": self.verification_passed,
            "details": dict(self.details),
        }


class ProactiveDecisionEngine:
    """Deterministic orchestration of local context -> decision -> execution -> verification."""

    ACTION_RISK = {
        "open_app": RiskLevel.LOW,
        "weather_report": RiskLevel.LOW,
        "web_search": RiskLevel.LOW,
        "reminder": RiskLevel.LOW,
        "file_controller": RiskLevel.MEDIUM,
        "browser_control": RiskLevel.MEDIUM,
        "computer_control": RiskLevel.MEDIUM,
        "send_message": RiskLevel.HIGH,
        "delete_file": RiskLevel.CRITICAL,
    }

    def __init__(
        self,
        *,
        action_registry: Optional[Any] = None,
        permissions: Optional[PermissionManager] = None,
        learning_memory: Optional[LearningMemory] = None,
        logger: Callable[[str], None] = print,
        clock: Optional[Callable[[], datetime]] = None,
        duplicate_window_seconds: int = 3600,
    ) -> None:
        self.action_registry = action_registry
        self.permissions = permissions or PermissionManager()
        self.learning_memory = learning_memory or LearningMemory()
        self.logger = logger
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.duplicate_window_seconds = max(1, int(duplicate_window_seconds))
        self._recent_decisions: Dict[str, datetime] = {}

    def _now(self) -> datetime:
        return self.clock()

    def _extract_items(self, context: Any) -> List[Mapping[str, Any]]:
        if context is None:
            return []
        if isinstance(context, Mapping):
            items = context.get("items")
            if items:
                return [Situation.normalize_item(item) for item in items]
            reports = context.get("reports") or []
            if reports:
                flattened: List[Mapping[str, Any]] = []
                for report in reports:
                    if isinstance(report, Mapping):
                        for item in report.get("items") or ():
                            flattened.append(Situation.normalize_item(item))
                return flattened
        items_attr = getattr(context, "items", None)
        if items_attr is not None and not callable(items_attr):
            items = list(items_attr) if not isinstance(items_attr, Mapping) else list(items_attr.values())
            if items:
                return [Situation.normalize_item(item) for item in items]
        reports_attr = getattr(context, "reports", None)
        if reports_attr is not None:
            flattened: List[Mapping[str, Any]] = []
            for report in list(reports_attr):
                if isinstance(report, Mapping):
                    for item in report.get("items") or ():
                        flattened.append(Situation.normalize_item(item))
                elif hasattr(report, "items"):
                    for item in getattr(report, "items") or ():
                        flattened.append(Situation.normalize_item(item))
            if flattened:
                return flattened
        return []

    def _risk_for_action(self, action: Optional[str]) -> RiskLevel:
        if not action:
            return RiskLevel.LOW
        return self.ACTION_RISK.get(action, RiskLevel.MEDIUM)

    def _decision_key(self, situation: Situation, action: Optional[str], parameters: Optional[Dict[str, Any]] = None) -> str:
        base = f"{situation.category}:{situation.summary}:{action or 'none'}:{str(parameters or {})}"
        return base.lower().replace(" ", "")

    def _detect_action(self, item: Mapping[str, Any]) -> Optional[str]:
        text = f"{item.get('title', '')} {item.get('content', '')}".lower()
        if "message" in text and "send" in text:
            return "send_message"
        if "delete" in text or "destroy" in text or "remove" in text:
            return "delete_file"
        if "calculator" in text or "visual studio code" in text or "browser" in text or "chrome" in text or "open" in text:
            return "open_app"
        if "weather" in text:
            return "weather_report"
        if "file" in text or "write" in text or "update" in text:
            return "file_controller"
        return None

    def decide(self, context: Any) -> Decision:
        items = self._extract_items(context)
        if not items:
            return Decision(
                state=DecisionState.NO_ACTION,
                situation=None,
                risk=RiskLevel.LOW,
                reason="No actionable local context detected.",
            )

        ranked = sorted(items, key=lambda item: self._priority_value(item), reverse=True)
        item = ranked[0]
        situation = Situation.from_item(item)
        action = self._detect_action(item)

        if situation.category == "reminder":
            state = DecisionState.INFORM
            risk = RiskLevel.LOW
            reminder_name = item.get("title") or item.get("content") or situation.summary
            reason = f"Important reminder approaching: {reminder_name}."
            action = None
        elif situation.category in {"action", "system", "task", "weather", "general", "priority"}:
            if action is not None:
                state = DecisionState.EXECUTE
                risk = self._risk_for_action(action)
                reason = f"Local context suggests {action} would help."
            elif situation.category == "system":
                state = DecisionState.PREPARE
                risk = RiskLevel.LOW
                reason = "System context is relevant but no safe automatic action is required."
            else:
                state = DecisionState.INFORM
                risk = RiskLevel.LOW
                reason = situation.summary
        elif situation.category == "communication":
            state = DecisionState.ASK_PERMISSION
            risk = RiskLevel.HIGH
            action = action or "send_message"
            reason = "External communication requires explicit approval."
        elif situation.category == "destructive":
            state = DecisionState.BLOCKED
            risk = RiskLevel.CRITICAL
            action = action or "delete_file"
            reason = "Critical or destructive action is blocked by policy."
        else:
            state = DecisionState.NO_ACTION
            risk = RiskLevel.LOW
            action = None
            reason = "No actionable local situation matched."

        if action is not None:
            parameters = {"action": action, "source": item.get("source", "context")}
            if state == DecisionState.BLOCKED or risk == RiskLevel.CRITICAL:
                return Decision(
                    state=DecisionState.BLOCKED,
                    situation=situation,
                    risk=RiskLevel.CRITICAL,
                    reason="Critical or destructive action is blocked by policy.",
                    action=action,
                    parameters=parameters,
                    requires_user_approval=False,
                    dedupe_key=self._decision_key(situation, action, parameters),
                )
            permission_decision = self.permissions.check(action, parameters)
            if not permission_decision.allowed:
                return Decision(
                    state=DecisionState.BLOCKED,
                    situation=situation,
                    risk=RiskLevel.CRITICAL if self._risk_for_action(action) == RiskLevel.CRITICAL else RiskLevel.HIGH,
                    reason=permission_decision.reason,
                    action=action,
                    parameters=parameters,
                    requires_user_approval=False,
                    dedupe_key=self._decision_key(situation, action, parameters),
                )
            if permission_decision.requires_confirmation:
                return Decision(
                    state=DecisionState.ASK_PERMISSION,
                    situation=situation,
                    risk=self._risk_for_action(action),
                    reason=permission_decision.reason,
                    action=action,
                    parameters=parameters,
                    requires_user_approval=True,
                    dedupe_key=self._decision_key(situation, action, parameters),
                )

        dedupe_key = self._decision_key(situation, action, {"source": item.get("source", "context")})
        now = self._now()
        last = self._recent_decisions.get(dedupe_key)
        if last is not None and (now - last).total_seconds() < self.duplicate_window_seconds:
            return Decision(
                state=DecisionState.NO_ACTION,
                situation=situation,
                risk=risk,
                reason=f"already handled: {situation.summary}",
                action=action,
                parameters={"source": item.get("source", "context")},
                dedupe_key=dedupe_key,
            )

        self._recent_decisions[dedupe_key] = now

        if state == DecisionState.EXECUTE:
            return Decision(
                state=DecisionState.EXECUTE,
                situation=situation,
                risk=risk,
                reason=reason,
                action=action,
                parameters={"source": item.get("source", "context")},
                requires_user_approval=False,
                dedupe_key=dedupe_key,
            )

        return Decision(
            state=state,
            situation=situation,
            risk=risk,
            reason=reason,
            action=action,
            parameters={"source": item.get("source", "context")},
            requires_user_approval=state == DecisionState.ASK_PERMISSION,
            dedupe_key=dedupe_key,
        )

    @staticmethod
    def _priority_value(item: Mapping[str, Any]) -> int:
        priority = str(item.get("priority") or "medium").lower()
        weights = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
        return weights.get(priority, 3)

    def execute_decision(self, decision: Decision, *, verification: Optional[Callable[[Any, Decision], bool]] = None) -> ActionResult:
        if decision.state in {DecisionState.NO_ACTION, DecisionState.INFORM, DecisionState.PREPARE}:
            return ActionResult(
                action_id=decision.action or "none",
                status="SUCCESS" if decision.state in {DecisionState.INFORM, DecisionState.PREPARE} else "UNKNOWN",
                started_at=self._now().isoformat(),
                completed_at=self._now().isoformat(),
                output=decision.reason,
                verification_required=False,
                verification_passed=True if decision.state in {DecisionState.INFORM, DecisionState.PREPARE} else None,
                details={"action_id": decision.action or "none", "decision_state": decision.state.value},
            )

        if decision.state in {DecisionState.ASK_PERMISSION, DecisionState.BLOCKED}:
            return ActionResult(
                action_id=decision.action or "blocked",
                status="AWAITING_PERMISSION" if decision.state == DecisionState.ASK_PERMISSION else "BLOCKED",
                started_at=self._now().isoformat(),
                completed_at=self._now().isoformat(),
                output="",
                error=decision.reason,
                verification_required=False,
                verification_passed=False,
                details={"action_id": decision.action or "blocked", "decision_state": decision.state.value},
            )

        action = decision.action
        if not action:
            return ActionResult(
                action_id="none",
                status="UNKNOWN",
                started_at=self._now().isoformat(),
                completed_at=self._now().isoformat(),
                output="",
                error="No action assigned to decision.",
                verification_required=False,
                verification_passed=False,
                details={"action_id": "none", "decision_state": decision.state.value},
            )

        if decision.risk == RiskLevel.CRITICAL:
            return ActionResult(
                action_id=action,
                status="BLOCKED",
                started_at=self._now().isoformat(),
                completed_at=self._now().isoformat(),
                output="",
                error=decision.reason,
                verification_required=False,
                verification_passed=False,
                details={"action_id": action, "decision_state": decision.state.value},
            )

        permission_decision = self.permissions.check(action, decision.parameters)
        if not permission_decision.allowed:
            return ActionResult(
                action_id=action,
                status="DENIED",
                started_at=self._now().isoformat(),
                completed_at=self._now().isoformat(),
                output="",
                error=permission_decision.reason,
                verification_required=False,
                verification_passed=False,
                details={"action_id": action, "decision_state": decision.state.value},
            )
        if permission_decision.requires_confirmation:
            return ActionResult(
                action_id=action,
                status="AWAITING_PERMISSION",
                started_at=self._now().isoformat(),
                completed_at=self._now().isoformat(),
                output="",
                error=permission_decision.reason,
                verification_required=False,
                verification_passed=False,
                details={"action_id": action, "decision_state": decision.state.value},
            )

        if self.action_registry is None or not self.action_registry.has(action):
            return ActionResult(
                action_id=action,
                status="UNKNOWN",
                started_at=self._now().isoformat(),
                completed_at=self._now().isoformat(),
                output="",
                error=f"Action '{action}' is not available in the local registry.",
                verification_required=False,
                verification_passed=False,
                details={"action_id": action, "decision_state": decision.state.value},
            )

        started = self._now()
        try:
            output = self.action_registry.run(action, decision.parameters, {})
        except Exception as exc:
            completed = self._now()
            result = ActionResult(
                action_id=action,
                status="FAILED",
                started_at=started.isoformat(),
                completed_at=completed.isoformat(),
                output="",
                error=f"{type(exc).__name__}: {exc}",
                verification_required=False,
                verification_passed=False,
                details={"action_id": action, "decision_state": decision.state.value},
            )
            self._record_learning(decision, result)
            return result

        status = "SUCCESS" if str(output).lower() and "failed" not in str(output).lower() else "FAILED"
        verified = True
        if verification is not None:
            try:
                verified = bool(verification(output, decision))
            except TypeError:
                verified = bool(verification())
            if not verified:
                status = "FAILED"
        if status == "SUCCESS" and verification is None and "failed" not in str(output).lower():
            verified = True
        completed = self._now()
        result = ActionResult(
            action_id=action,
            status=status,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            output=str(output),
            error="" if status == "SUCCESS" else "Verification failed." if verification is not None and not verified else "Action failed.",
            verification_required=verification is not None,
            verification_passed=verified,
            details={"action_id": action, "outcome": output, "decision_state": decision.state.value},
        )
        self._record_learning(decision, result)
        return result

    def _record_learning(self, decision: Decision, result: ActionResult) -> None:
        try:
            if self.learning_memory is None:
                return
            goal = decision.situation.summary if decision.situation else (decision.reason or decision.action or "local action")
            if hasattr(self.learning_memory, "learn"):
                self.learning_memory.learn(
                    name=f"proactive_{(decision.action or 'decision').replace(' ', '_')}_{int(self._now().timestamp())}",
                    goal=goal,
                    steps=[
                        {
                            "action": decision.action or "no_action",
                            "parameters": decision.parameters,
                            "description": decision.reason,
                        }
                    ],
                )
            elif hasattr(self.learning_memory, "record"):
                self.learning_memory.record(
                    {
                        "situation": goal,
                        "decision": decision.state.value,
                        "action": decision.action,
                        "result": result.status,
                    }
                )
        except Exception:
            # Learning is best-effort and must never break the local decision loop.
            pass


__all__ = [
    "ActionResult",
    "Decision",
    "DecisionState",
    "ProactiveDecisionEngine",
    "RiskLevel",
    "Situation",
]
