"""Evidence-backed, local-only capability registry and roadmap builder.

This module is the read-only analysis layer between repository inspection and
the existing controlled engineering Director.  It never edits source, creates a
worktree, or starts an implementation: a plan produced here must pass its own
explicit approval gate before it can be handed to the local engineering
lifecycle.

Everything here runs on the local machine.  The Cloud service, its API surface,
and any remote persistence are deliberately *not* dependencies: discovery reads
the local working tree, approval state is stored in the existing local runtime
files, and the only execution path it can trigger is the already-existing local
self-engineering Director.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .capability_analyzer import CapabilityAnalyzer
from .capability_store import CapabilityStore
from .models import CapabilityAnalysis, RepositorySnapshot, utc_now
from .policies import ApprovalRequired, EngineeringOperation, EngineeringPolicy
from .repository_inspector import RepositoryInspector
from automation.permission_manager import PermissionManager


class CapabilityStatus(str, Enum):
    """Discovered availability of one capability.

    ``PARTIAL`` means some evidence exists but the capability boundary is
    incomplete.  ``PLANNED`` is an unimplemented capability that the local
    roadmap document already tracks.  ``BLOCKED`` means local policy or a
    missing prerequisite prevents progress regardless of engineering effort.
    """

    AVAILABLE = "available"
    PARTIAL = "partial"
    MISSING = "missing"
    BLOCKED = "blocked"
    PLANNED = "planned"

    @property
    def is_available(self) -> bool:
        return self == CapabilityStatus.AVAILABLE

    @property
    def requires_engineering(self) -> bool:
        return self in {
            CapabilityStatus.PARTIAL,
            CapabilityStatus.MISSING,
            CapabilityStatus.PLANNED,
        }


class CapabilityPlanStatus(str, Enum):
    AWAITING_CAPABILITY_APPROVAL = "awaiting_capability_approval"
    APPROVED = "approved"
    CANCELLED = "cancelled"


class CapabilityDecision(str, Enum):
    """The three operator decisions exposed by the capability approval gate."""

    APPROVE = "approve"
    MODIFY = "modify"
    CANCEL = "cancel"


@dataclass(frozen=True)
class CapabilityDefinition:
    """Static metadata for one capability; status is always discovered."""

    capability_id: str
    name: str
    description: str
    evidence_paths: Tuple[str, ...] = ()
    dependencies: Tuple[str, ...] = ()
    permissions: Tuple[str, ...] = ()
    risk: str = "low"
    tests: Tuple[str, ...] = ()
    implementation: str = "Reuse the existing capability boundary."
    integrations: Tuple[str, ...] = ()
    roadmap_item: bool = False
    roadmap_aliases: Tuple[str, ...] = ()
    action: str = ""
    discovered: bool = False



@dataclass
class CapabilitySpec:
    """One discovered capability with its evidence-backed availability."""

    capability_id: str
    name: str
    description: str
    status: CapabilityStatus
    implementation: str
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    risk: str = "low"
    tests: List[str] = field(default_factory=list)
    integrations: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    action: str = ""
    blocked_reason: str = ""
    roadmap_reference: str = ""
    discovered: bool = False

    @property
    def availability(self) -> str:
        return self.status.value

    @property
    def is_available(self) -> bool:
        return self.status.is_available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "availability": self.availability,
            "implementation": self.implementation,
            "dependencies": list(self.dependencies),
            "permissions": list(self.permissions),
            "risk": self.risk,
            "tests": list(self.tests),
            "integrations": list(self.integrations),
            "evidence": list(self.evidence),
            "missing_evidence": list(self.missing_evidence),
            "action": self.action,
            "blocked_reason": self.blocked_reason,
            "roadmap_reference": self.roadmap_reference,
            "discovered": self.discovered,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CapabilitySpec":
        """Rebuild a spec from persisted JSON without trusting its shape."""
        if not isinstance(raw, Mapping):
            raise ValueError("Persisted capability entries must be objects.")
        capability_id = str(raw.get("capability_id") or "").strip()
        if not capability_id:
            raise ValueError("Persisted capability entries require a capability_id.")
        try:
            status = CapabilityStatus(str(raw.get("status", CapabilityStatus.MISSING.value)))
        except ValueError:
            status = CapabilityStatus.MISSING

        def strings(key: str) -> List[str]:
            value = raw.get(key)
            return [str(item) for item in value] if isinstance(value, list) else []

        return cls(
            capability_id=capability_id,
            name=str(raw.get("name") or capability_id.replace("_", " ").title()),
            description=str(raw.get("description") or ""),
            status=status,
            implementation=str(raw.get("implementation") or "Reuse the existing capability boundary."),
            dependencies=strings("dependencies"),
            permissions=strings("permissions"),
            risk=str(raw.get("risk") or "low"),
            tests=strings("tests"),
            integrations=strings("integrations"),
            evidence=strings("evidence"),
            missing_evidence=strings("missing_evidence"),
            action=str(raw.get("action") or ""),
            blocked_reason=str(raw.get("blocked_reason") or ""),
            roadmap_reference=str(raw.get("roadmap_reference") or ""),
            discovered=bool(raw.get("discovered", False)),
        )


@dataclass
class CapabilityGraph:
    """A dependency graph with deterministic dependency-first ordering."""

    nodes: List[str]
    edges: Dict[str, List[str]]
    implementation_order: List[str]
    statuses: Dict[str, str] = field(default_factory=dict)
    blocked: List[str] = field(default_factory=list)
    cycles: List[List[str]] = field(default_factory=list)

    def status_of(self, capability_id: str) -> str:
        return self.statuses.get(capability_id, CapabilityStatus.MISSING.value)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": list(self.nodes),
            "edges": {key: list(value) for key, value in self.edges.items()},
            "implementation_order": list(self.implementation_order),
            "statuses": dict(self.statuses),
            "blocked": list(self.blocked),
            "cycles": [list(cycle) for cycle in self.cycles],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CapabilityGraph":
        if not isinstance(raw, Mapping):
            raise ValueError("Persisted dependency graphs must be objects.")

        def strings(key: str) -> List[str]:
            value = raw.get(key)
            return [str(item) for item in value] if isinstance(value, list) else []

        edges_raw = raw.get("edges")
        edges = (
            {str(key): [str(item) for item in value] for key, value in edges_raw.items() if isinstance(value, list)}
            if isinstance(edges_raw, Mapping)
            else {}
        )
        statuses_raw = raw.get("statuses")
        statuses = (
            {str(key): str(value) for key, value in statuses_raw.items()}
            if isinstance(statuses_raw, Mapping)
            else {}
        )
        cycles_raw = raw.get("cycles")
        cycles = (
            [[str(item) for item in cycle] for cycle in cycles_raw if isinstance(cycle, list)]
            if isinstance(cycles_raw, list)
            else []
        )
        return cls(
            nodes=strings("nodes"),
            edges=edges,
            implementation_order=strings("implementation_order"),
            statuses=statuses,
            blocked=strings("blocked"),
            cycles=cycles,
        )



@dataclass
class CapabilityPlan:
    """A reviewable capability roadmap that stops at the approval gate."""

    analysis_id: str
    goal: str
    interpretation: str
    status: str
    created_at: str
    repository_snapshot: Dict[str, Any]
    registry: List[CapabilitySpec]
    current_capabilities: List[CapabilitySpec]
    partial_capabilities: List[CapabilitySpec]
    missing_capabilities: List[CapabilitySpec]
    planned_capabilities: List[CapabilitySpec]
    dependency_graph: CapabilityGraph
    dependencies: List[str]
    risks: List[str]
    proposed_architecture: List[Dict[str, Any]]
    required_integrations: List[str]
    required_permissions: List[str]
    estimated_phases: List[Dict[str, Any]]
    test_strategy: List[str]
    rollback_strategy: str
    deployment_strategy: str
    blocked_capabilities: List[CapabilitySpec] = field(default_factory=list)
    approval_required: bool = True
    approval_reason: str = "Capability implementation is blocked until an operator approves this plan."
    revision: int = 1
    modifications: List[Dict[str, Any]] = field(default_factory=list)
    deferred_capabilities: List[str] = field(default_factory=list)
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    cancelled_at: Optional[str] = None
    cancelled_reason: str = ""
    engineering_task_id: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)

    # ----------------------------------------------------------------
    # Status helpers
    # ----------------------------------------------------------------

    @property
    def is_approved(self) -> bool:
        return self.status == CapabilityPlanStatus.APPROVED.value

    @property
    def is_awaiting_approval(self) -> bool:
        return self.status == CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value

    @property
    def is_cancelled(self) -> bool:
        return self.status == CapabilityPlanStatus.CANCELLED.value

    def roadmap_targets(self) -> List[CapabilitySpec]:
        """Capabilities this plan proposes to build, in dependency-first order.

        ``missing_capabilities`` already contains both the unexpectedly absent
        capabilities and the roadmap-tracked ones; deferred capabilities are
        excluded because the operator removed them from this revision.
        """
        deferred = set(self.deferred_capabilities)
        by_id = {
            item.capability_id: item
            for item in [*self.partial_capabilities, *self.missing_capabilities]
        }
        targets: List[CapabilitySpec] = []
        for capability_id in self.dependency_graph.implementation_order:
            spec = by_id.get(capability_id)
            if spec is None or capability_id in deferred:
                continue
            targets.append(spec)
        return targets

    def record(self, stage: str, message: str, **metadata: Any) -> None:
        self.events.append({
            "stage": stage,
            "message": message,
            "timestamp": utc_now(),
            "metadata": dict(metadata),
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "task_id": self.analysis_id,
            "goal": self.goal,
            "interpretation": self.interpretation,
            "status": self.status,
            "created_at": self.created_at,
            "revision": self.revision,
            "repository_snapshot": dict(self.repository_snapshot),
            "registry": [item.to_dict() for item in self.registry],
            "current_capabilities": [item.to_dict() for item in self.current_capabilities],
            "partial_capabilities": [item.to_dict() for item in self.partial_capabilities],
            "missing_capabilities": [item.to_dict() for item in self.missing_capabilities],
            "planned_capabilities": [item.to_dict() for item in self.planned_capabilities],
            "blocked_capabilities": [item.to_dict() for item in self.blocked_capabilities],
            "roadmap_targets": [item.capability_id for item in self.roadmap_targets()],
            "deferred_capabilities": list(self.deferred_capabilities),
            "dependency_graph": self.dependency_graph.to_dict(),
            "dependencies": list(self.dependencies),
            "risks": list(self.risks),
            "proposed_architecture": list(self.proposed_architecture),
            "required_integrations": list(self.required_integrations),
            "required_permissions": list(self.required_permissions),
            "estimated_phases": list(self.estimated_phases),
            "test_strategy": list(self.test_strategy),
            "rollback_strategy": self.rollback_strategy,
            "deployment_strategy": self.deployment_strategy,
            "approval_required": self.approval_required,
            "approval_reason": self.approval_reason,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "cancelled_at": self.cancelled_at,
            "cancelled_reason": self.cancelled_reason,
            "modifications": [dict(item) for item in self.modifications],
            "engineering_task_id": self.engineering_task_id,
            "events": [dict(item) for item in self.events],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CapabilityPlan":
        """Rebuild a plan from persisted JSON, ignoring unusable entries."""
        if not isinstance(raw, Mapping):
            raise ValueError("Persisted capability plans must be objects.")
        analysis_id = str(raw.get("analysis_id") or raw.get("task_id") or "").strip()
        goal = str(raw.get("goal") or "").strip()
        if not analysis_id or not goal:
            raise ValueError("Persisted capability plans require an analysis_id and a goal.")

        def strings(key: str) -> List[str]:
            value = raw.get(key)
            return [str(item) for item in value] if isinstance(value, list) else []

        def dictionaries(key: str) -> List[Dict[str, Any]]:
            value = raw.get(key)
            return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

        def specs(key: str) -> List[CapabilitySpec]:
            items: List[CapabilitySpec] = []
            for entry in dictionaries(key):
                try:
                    items.append(CapabilitySpec.from_dict(entry))
                except ValueError:
                    continue
            return items

        snapshot = raw.get("repository_snapshot")
        graph = raw.get("dependency_graph")
        try:
            revision = max(1, int(raw.get("revision", 1)))
        except (TypeError, ValueError):
            revision = 1
        return cls(
            analysis_id=analysis_id,
            goal=goal,
            interpretation=str(raw.get("interpretation") or ""),
            status=str(raw.get("status") or CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value),
            created_at=str(raw.get("created_at") or utc_now()),
            repository_snapshot=dict(snapshot) if isinstance(snapshot, Mapping) else {},
            registry=specs("registry"),
            current_capabilities=specs("current_capabilities"),
            partial_capabilities=specs("partial_capabilities"),
            missing_capabilities=specs("missing_capabilities"),
            planned_capabilities=specs("planned_capabilities"),
            blocked_capabilities=specs("blocked_capabilities"),
            dependency_graph=CapabilityGraph.from_dict(graph if isinstance(graph, Mapping) else {}),
            dependencies=strings("dependencies"),
            risks=strings("risks"),
            proposed_architecture=dictionaries("proposed_architecture"),
            required_integrations=strings("required_integrations"),
            required_permissions=strings("required_permissions"),
            estimated_phases=dictionaries("estimated_phases"),
            test_strategy=strings("test_strategy"),
            rollback_strategy=str(raw.get("rollback_strategy") or ""),
            deployment_strategy=str(raw.get("deployment_strategy") or ""),
            approval_required=bool(raw.get("approval_required", True)),
            approval_reason=str(raw.get("approval_reason") or ""),
            revision=revision,
            modifications=dictionaries("modifications"),
            deferred_capabilities=strings("deferred_capabilities"),
            approved_at=raw.get("approved_at") if isinstance(raw.get("approved_at"), str) else None,
            approved_by=raw.get("approved_by") if isinstance(raw.get("approved_by"), str) else None,
            cancelled_at=raw.get("cancelled_at") if isinstance(raw.get("cancelled_at"), str) else None,
            cancelled_reason=str(raw.get("cancelled_reason") or ""),
            engineering_task_id=raw.get("engineering_task_id") if isinstance(raw.get("engineering_task_id"), str) else None,
            events=dictionaries("events"),
        )


class CapabilityRegistry:
    """Catalog and evidence matcher for the capability surface of JARVIS."""

    ROADMAP_PATH = "docs/proactive-roadmap.json"
    MAX_ROADMAP_BYTES = 64_000
    ROADMAP_SECTIONS = ("current", "missing", "planned")
    DISCOVERABLE_PREFIX = "actions/"

    CATALOG: Tuple[CapabilityDefinition, ...] = (
        CapabilityDefinition("reasoning", "Reasoning", "LLM-backed reasoning and response generation.", ("cloud/ai_router.py", "core/llm_client.py"), permissions=("provider access",), risk="medium", tests=("provider failure", "redaction"), integrations=("LLM provider",)),
        CapabilityDefinition("planning", "Planning", "Goal decomposition into executable plans.", ("automation/autonomous_planner.py", "automation/autonomous_engine.py"), dependencies=("reasoning",), permissions=("plan approval",), risk="medium", tests=("plan validation", "fallback planning")),
        CapabilityDefinition("task_execution", "Task execution", "Controlled execution through registered actions and agents.", ("automation/autonomous_engine.py", "core/action_loader.py"), dependencies=("planning", "permissions"), permissions=("action approval",), risk="medium", tests=("action dispatch", "lease recovery")),
        CapabilityDefinition("memory", "Memory", "Persistent short- and long-term assistant memory.", ("automation/learning_memory.py", "memory/memory_manager.py"), permissions=("memory retention",), risk="medium", tests=("redaction", "retention")),
        CapabilityDefinition("permissions", "Permission management", "Explicit approval boundaries for sensitive actions.", ("automation/permission_manager.py", "core/confirm.py"), risk="high", tests=("protected paths", "approval denial")),
        CapabilityDefinition("learning", "Learning", "Reusable workflow and engineering change memory.", ("automation/learning_memory.py", "automation/self_engineering/change_memory.py"), dependencies=("memory",), permissions=("workflow retention",), risk="medium", tests=("exact context matching", "incompatible context rejection")),
        CapabilityDefinition("application_control", "Application control", "Open and control desktop applications.", ("actions/open_app.py", "actions/computer_control.py"), dependencies=("permissions",), permissions=("desktop control",), risk="high", tests=("permission gate",)),
        CapabilityDefinition("browser_control", "Browser control", "Automated browser navigation and interaction.", ("actions/browser_control.py",), dependencies=("permissions",), permissions=("browser control",), risk="high", tests=("URL validation",)),
        CapabilityDefinition("filesystem_control", "Filesystem control", "Bounded file read and write actions.", ("actions/file_controller.py", "actions/file_processor.py"), dependencies=("permissions",), permissions=("filesystem access",), risk="high", tests=("path boundary", "secret protection")),
        CapabilityDefinition("desktop_control", "Desktop control", "Desktop and screen interaction primitives.", ("actions/desktop.py", "actions/screen_processor.py"), dependencies=("permissions",), permissions=("screen access",), risk="high", tests=("screen boundary",), action="desktop_control"),
        CapabilityDefinition("system_settings", "System settings", "Read or change approved computer settings.", ("actions/computer_settings.py",), dependencies=("permissions",), permissions=("system settings",), risk="high", tests=("approval required",)),
        CapabilityDefinition("web_search", "Web search", "Search the public web through a registered action.", ("actions/web_search.py",), dependencies=("permissions",), risk="medium", tests=("query redaction",)),
        CapabilityDefinition("weather", "Weather", "Weather lookup for contextual reports.", ("actions/weather_report.py",), dependencies=("web_search",), risk="low", tests=("provider outage",)),
        CapabilityDefinition("youtube", "YouTube", "YouTube search and playback support.", ("actions/youtube_video.py",), dependencies=("browser_control",), risk="medium", tests=("URL validation",)),
        CapabilityDefinition("messaging", "Messaging", "User-directed message sending action.", ("actions/send_message.py",), dependencies=("permissions",), permissions=("send message",), risk="high", tests=("recipient confirmation",)),
        CapabilityDefinition("notifications", "Local notification primitives", "In-process reminder events and spoken notifications.", ("actions/reminder.py", "core/tts.py"), dependencies=("permissions",), permissions=("notification consent",), risk="medium", tests=("duplicate reminder",), action="reminder"),
        CapabilityDefinition("voice", "Voice interaction", "Local speech input, wake word, and spoken output.", ("core/stt.py", "core/tts.py", "core/wake_word.py", "dashboard/static/hud/js/voice.js"), dependencies=("permissions",), permissions=("microphone",), risk="medium", tests=("wake word gating", "provider failure")),
        CapabilityDefinition("reminders", "Reminders", "Reminder creation and task follow-up.", ("actions/reminder.py",), dependencies=("notifications",), permissions=("reminder consent",), risk="medium", tests=("time zones",)),
        CapabilityDefinition("calendar_integration", "Calendar integration", "Read calendar events through a consented provider connector.", ("integrations/calendar.py", "actions/calendar.py"), dependencies=("permissions",), permissions=("calendar read scope", "account consent"), risk="high", tests=("OAuth expiry", "provider errors"), integrations=("OAuth client", "calendar API"), roadmap_item=True),
        CapabilityDefinition("email_integration", "Email integration", "Read email summaries through a consented provider connector.", ("integrations/email.py", "actions/email.py"), dependencies=("permissions",), permissions=("email read scope", "account consent"), risk="critical", tests=("secret redaction", "provider outage"), integrations=("OAuth client", "email API"), roadmap_item=True),
        CapabilityDefinition("scheduling", "Scheduling", "Durable time- and timezone-aware scheduling above reminder triggers.", ("actions/reminder.py", "scheduler.py"), dependencies=("permissions",), permissions=("recurring schedule consent",), risk="medium", tests=("time zones", "restart recovery"), roadmap_item=True, roadmap_aliases=("scheduler",), action="reminder"),
        CapabilityDefinition("task_management", "Task lifecycle", "Local task lifecycle, execution history, and learned workflows.", ("automation/autonomous_engine.py", "automation/learning_memory.py"), dependencies=("permissions",), risk="medium", tests=("lease recovery", "cancellation")),
        CapabilityDefinition("context_aggregator", "Context aggregator", "Normalize relevant personal and environmental context for a briefing.", ("automation/context_aggregator.py", "integrations/context_aggregator.py", "integrations/calendar.py", "integrations/email.py"), dependencies=("memory", "calendar_integration", "email_integration", "weather"), permissions=("per-provider read scopes", "data retention"), risk="high", tests=("redaction", "stale data", "provider outage"), integrations=("calendar API", "email API", "weather API"), roadmap_item=True, roadmap_aliases=("context aggregator",)),
        CapabilityDefinition("proactive_scheduler", "Proactive scheduler", "Trigger approved briefings at durable, timezone-aware schedules.", ("scheduler.py", "automation/scheduler.py"), dependencies=("scheduling", "permissions", "notification_layer"), permissions=("recurring notification consent", "quiet hours"), risk="high", tests=("duplicate trigger prevention", "restart recovery", "time zones"), roadmap_item=True, roadmap_aliases=("proactive scheduler", "scheduler")),
        CapabilityDefinition("notification_layer", "Proactive notification orchestration", "Deliver approved notifications with quiet hours and delivery status.", ("automation/notification_layer.py", "notifications/service.py"), dependencies=("notifications", "task_management"), permissions=("per-channel consent", "quiet hours"), risk="high", tests=("deduplication", "offline device", "rate limiting"), integrations=("device presence",), roadmap_item=True, roadmap_aliases=("notification layer",)),
        CapabilityDefinition("briefing_generator", "Personalized briefing engine", "Compose a bounded morning briefing from normalized context.", ("automation/briefing_generator.py", "automation/briefing_engine.py"), dependencies=("context_aggregator", "memory", "reasoning"), permissions=("briefing preference access",), risk="high", tests=("empty context", "prompt safety", "timezone boundaries"), roadmap_item=True, roadmap_aliases=("personalized briefing engine", "briefing engine")),
        CapabilityDefinition("voice_delivery", "Voice briefing delivery", "Deliver an approved briefing through the local voice channel.", ("automation/briefing_delivery.py",), dependencies=("briefing_generator", "voice", "notification_layer"), permissions=("voice output",), risk="high", tests=("offline voice provider", "interrupt handling")),
        CapabilityDefinition("proactive_briefing", "Proactive morning briefing", "End-to-end scheduled, personalized morning briefing capability.", ("automation/morning_briefing.py", "automation/briefing_service.py"), dependencies=("proactive_scheduler", "context_aggregator", "briefing_generator", "voice_delivery", "proactive_execution"), permissions=("briefing schedule consent", "per-provider read scopes"), risk="high", tests=("empty context", "provider outage", "duplicate delivery", "rollback"), roadmap_item=True, roadmap_aliases=("proactive briefing", "morning briefing")),
        CapabilityDefinition("self_inspection", "Self-inspection", "Bounded AST and Git repository inspection.", ("automation/self_engineering/repository_inspector.py",), dependencies=("permissions",), risk="low", tests=("bounded traversal", "no code execution")),
        CapabilityDefinition("self_engineering", "Self-engineering", "Controlled engineering lifecycle orchestration.", ("automation/self_engineering/director.py", "automation/self_engineering/engineering_planner.py"), dependencies=("self_inspection", "permissions"), permissions=("engineering approval",), risk="high", tests=("approval gate", "lifecycle trace")),
        CapabilityDefinition("sandbox", "Isolated worktrees", "Git worktree isolation for autonomous changes.", ("automation/self_engineering/sandbox_manager.py",), dependencies=("self_engineering",), risk="high", tests=("production unchanged", "safe cleanup")),
        CapabilityDefinition("testing", "Test execution", "Bounded compilation and test execution in the sandbox.", ("automation/self_engineering/test_runner.py",), dependencies=("sandbox",), risk="medium", tests=("failure capture", "timeout")),
        CapabilityDefinition("repair", "Autonomous repair", "Failure analysis and bounded repair attempts.", ("automation/self_engineering/failure_analyzer.py", "automation/self_engineering/repair_engine.py"), dependencies=("testing", "sandbox"), risk="high", tests=("attempt limit", "repair trace")),
        CapabilityDefinition("git_checkpoint", "Git checkpoints", "Checkpoint and rollback support for isolated engineering runs.", ("automation/self_engineering/git_manager.py",), dependencies=("sandbox",), risk="high", tests=("checkpoint", "rollback")),
        CapabilityDefinition("deployment_verification", "Deployment verification", "Verification and approval gating before production deployment.", ("automation/self_engineering/deployment_verifier.py",), dependencies=("git_checkpoint", "permissions"), permissions=("deployment approval",), risk="critical", tests=("production unchanged", "verification")),
        CapabilityDefinition("capability_builder", "Capability analysis", "Local capability inventory, gap analysis, dependency graph, and approval-gated roadmap.", ("automation/self_engineering/capability_builder.py", "automation/self_engineering/capability_store.py"), dependencies=("self_inspection", "permissions"), risk="medium", tests=("gap analysis", "dependency ordering", "approval gate")),
        CapabilityDefinition("proactive_execution", "Proactive execution", "Unprompted prompting with silence and cooldown gates; durable delivery is absent.", ("actions/proactive.py", "actions/background_monitor.py", "actions/system_monitor.py", "automation/scheduler.py"), dependencies=("permissions", "notifications"), permissions=("proactive prompting consent",), risk="medium", tests=("silence gate", "cooldown", "quiet hours")),
        CapabilityDefinition("background_monitoring", "Background monitoring", "Local background context and system monitoring.", ("actions/background_monitor.py", "actions/system_monitor.py", "actions/screen_processor.py"), dependencies=("permissions", "memory"), permissions=("background capture",), risk="medium", tests=("monitor throttle", "redaction")),
        CapabilityDefinition("code_assistance", "Local code assistance", "Bounded repository helpers and developer diagnostics.", ("actions/code_helper.py", "actions/dev_agent.py"), dependencies=("permissions",), permissions=("code assistance",), risk="medium", tests=("path boundary",), action="code_helper"),
        CapabilityDefinition("infrastructure_management", "Infrastructure management", "Approved infrastructure status and deployment actions.", ("actions/infrastructure.py",), dependencies=("permissions",), permissions=("infrastructure approval",), risk="high", tests=("approval denial",), action="infrastructure"),
        CapabilityDefinition("flight_search", "Flight search", "Flight lookup through the existing web tooling.", ("actions/flight_finder.py",), dependencies=("web_search",), risk="low", tests=("provider outage",), action="flight_finder"),
        CapabilityDefinition("game_updates", "Game updates", "Local game library update checks.", ("actions/game_updater.py",), dependencies=("permissions",), permissions=("game update approval",), risk="medium", tests=("approval required",), action="game_updater"),
        CapabilityDefinition("mac_agent", "Mac Agent", "Authenticated local worker that executes queued device tasks.", ("agent/mac_agent.py", "agent/mac_agent_outbox.json"), dependencies=("permissions", "task_management"), permissions=("device worker",), risk="high", tests=("authenticated headers", "outbox redaction")),
        CapabilityDefinition("hud", "Local command center", "Local dashboard surface for tasks, activity, and engineering state.", ("dashboard/static/jarvis-command-center.html",), risk="low", tests=("panel rendering",)),
        CapabilityDefinition("telephony", "Telephony", "Consented call lifecycle with recording controls and an audit trail.", ("integrations/telephony.py", "actions/call.py"), dependencies=("permissions", "notification_layer"), permissions=("per-call confirmation",), risk="critical", tests=("webhook authentication", "duplicate callbacks"), roadmap_item=True, roadmap_aliases=("telephony and call lifecycle",)),
        CapabilityDefinition("conversation_continuity", "Conversation continuity", "Session resume, forget, and export controls.", ("automation/session_continuity.py",), dependencies=("memory", "permissions"), permissions=("retention controls",), risk="high", tests=("redaction", "deletion"), roadmap_item=True, roadmap_aliases=("conversation continuity",)),
        CapabilityDefinition("briefing_preferences", "Briefing preferences", "Stored per-user briefing windows, channels, and quiet hours.", ("automation/briefing_preferences.py",), dependencies=("permissions", "memory"), permissions=("preference storage",), risk="medium", tests=("default window", "quiet hours")),
    )

    def __init__(
        self,
        definitions: Sequence[CapabilityDefinition] | None = None,
        *,
        permission_manager: Optional[PermissionManager] = None,
        discover_actions: bool = True,
    ) -> None:
        selected = definitions if definitions is not None else self.CATALOG
        self.definitions: Dict[str, CapabilityDefinition] = {item.capability_id: item for item in selected}
        self.permission_manager = permission_manager
        self.discover_actions = discover_actions

    @staticmethod
    def _paths(snapshot: RepositorySnapshot) -> set[str]:
        paths = set(snapshot.tree)
        paths.update(module.path for module in snapshot.python_modules)
        return paths

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value).lower())

    def _roadmap_entries(self, snapshot: RepositorySnapshot) -> List[str]:
        """Read the local roadmap document when the repository already has one."""
        path = Path(snapshot.root) / self.ROADMAP_PATH
        try:
            if path.stat().st_size > self.MAX_ROADMAP_BYTES:
                return []
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, Mapping):
            return []
        entries: List[str] = []
        for section in self.ROADMAP_SECTIONS:
            value = raw.get(section)
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, Mapping) and isinstance(item.get("capability"), str):
                    entries.append(item["capability"])
        return entries

    def _roadmap_reference(self, definition: CapabilityDefinition, entries: Sequence[str]) -> str:
        """Match a capability against documented roadmap items without hardcoding."""
        keys = {
            self._normalize(value)
            for value in (definition.capability_id, definition.name, *definition.roadmap_aliases)
            if value
        }
        for entry in entries:
            candidate = self._normalize(entry)
            if not candidate:
                continue
            for key in keys:
                if key and (key == candidate or key in candidate or candidate in key):
                    return entry
        return ""

    def _permission_block(self, action: str) -> str:
        """Return why local permission policy prevents this capability, if it does."""
        if not action or self.permission_manager is None:
            return ""
        try:
            decision = self.permission_manager.check(action)
        except Exception:  # a broken permission store must never break analysis
            return ""
        if decision.allowed:
            return ""
        return f"the local permission policy blocks '{action}': {decision.reason}"

    def discover(self, snapshot: RepositorySnapshot) -> List[CapabilitySpec]:
        """Inventory catalog capabilities plus repository-discovered actions."""
        paths = self._paths(snapshot)
        roadmap = self._roadmap_entries(snapshot)
        specs: List[CapabilitySpec] = [
            self._catalog_spec(definition, paths, roadmap)
            for definition in self.definitions.values()
        ]
        if self.discover_actions:
            known = {item.capability_id for item in specs}
            specs.extend(self._discovered_specs(snapshot, known))
        return specs

    def _catalog_spec(
        self,
        definition: CapabilityDefinition,
        paths: set[str],
        roadmap: Sequence[str],
    ) -> CapabilitySpec:
        evidence = [path for path in definition.evidence_paths if path in paths]
        missing_evidence = [path for path in definition.evidence_paths if path not in paths]
        roadmap_reference = self._roadmap_reference(definition, roadmap)
        if evidence and not missing_evidence:
            status = CapabilityStatus.AVAILABLE
        elif evidence:
            status = CapabilityStatus.PARTIAL
        elif roadmap_reference:
            status = CapabilityStatus.PLANNED
        else:
            status = CapabilityStatus.MISSING
        blocked_reason = self._permission_block(definition.action)
        if blocked_reason:
            status = CapabilityStatus.BLOCKED
        return CapabilitySpec(
            capability_id=definition.capability_id,
            name=definition.name,
            description=definition.description,
            status=status,
            implementation=definition.implementation,
            dependencies=list(definition.dependencies),
            permissions=list(definition.permissions),
            risk=definition.risk,
            tests=list(definition.tests),
            integrations=list(definition.integrations),
            evidence=evidence,
            missing_evidence=missing_evidence,
            action=definition.action,
            blocked_reason=blocked_reason,
            roadmap_reference=roadmap_reference,
            discovered=definition.discovered,
        )

    def _discovered_specs(self, snapshot: RepositorySnapshot, known: set[str]) -> List[CapabilitySpec]:
        """Inventory registered actions from the repository instead of a list.

        Every ``actions/*.py`` module that declares a module-level ``TOOL`` is a
        real, locally available capability.  Discovery reuses the existing
        permission policy for its risk and approval metadata, so a capability
        that the user blocked locally is reported as blocked rather than as
        available.
        """
        specs: List[CapabilitySpec] = []
        for module in sorted(snapshot.python_modules, key=lambda item: item.path):
            tool = (module.tool_name or "").strip()
            if not tool or tool in known or not module.path.startswith(self.DISCOVERABLE_PREFIX):
                continue
            known.add(tool)
            decision = None
            if self.permission_manager is not None:
                try:
                    decision = self.permission_manager.check(tool)
                except Exception:  # a broken permission store must never break discovery
                    decision = None
            allowed = bool(getattr(decision, "allowed", True))
            requires_confirmation = bool(getattr(decision, "requires_confirmation", True))
            specs.append(CapabilitySpec(
                capability_id=tool,
                name=tool.replace("_", " ").title(),
                description=module.tool_description or f"Locally registered action '{tool}'.",
                status=CapabilityStatus.AVAILABLE if allowed else CapabilityStatus.BLOCKED,
                implementation="Reuse the registered action through the existing ActionRegistry.",
                dependencies=["permissions"] if requires_confirmation else [],
                permissions=["action approval"] if requires_confirmation else [],
                risk="medium" if requires_confirmation else "low",
                evidence=[module.path],
                blocked_reason="" if allowed else f"the local permission policy blocks '{tool}'",
                action=tool,
                discovered=True,
            ))
        return sorted(specs, key=lambda item: item.capability_id)


class CapabilityBuilder:
    """Build a truthful, local capability roadmap from the live repository."""

    GOAL_ROOTS: Mapping[str, Tuple[str, ...]] = {
        "proactive": ("proactive_briefing", "proactive_execution"),
        "morning": ("proactive_briefing",),
        "briefing": ("proactive_briefing",),
        "calendar": ("calendar_integration",),
        "email": ("email_integration",),
        "phone": ("telephony",),
        "call": ("telephony",),
        "telephony": ("telephony",),
        "conversation": ("conversation_continuity",),
        "continuity": ("conversation_continuity",),
        "weather": ("weather",),
    }

    DEFAULT_GOAL_ROOTS: Tuple[str, ...] = ("planning", "capability_builder", "testing")
    MAX_PHASE_SIZE = 4
    ENGINEERING_APPROVAL = "approve sandbox implementation and git checkpoint"
    MODULE_TEMPLATE = "automation/{capability_id}.py"

    def __init__(
        self,
        root: Path,
        *,
        inspector: RepositoryInspector | None = None,
        analyzer: CapabilityAnalyzer | None = None,
        registry: CapabilityRegistry | None = None,
        policy: EngineeringPolicy | None = None,
        store: CapabilityStore | None = None,
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.inspector = inspector or RepositoryInspector(self.root)
        self.analyzer = analyzer or CapabilityAnalyzer()
        self.policy = policy or EngineeringPolicy(self.root, permission_manager)
        self.permission_manager = permission_manager or getattr(self.policy, "permission_manager", None)
        self.registry = registry or CapabilityRegistry(permission_manager=self.permission_manager)
        self.store = store if store is not None else CapabilityStore()

    def _roots_for_goal(self, goal: str) -> Tuple[str, ...]:
        lower = goal.lower()
        roots: List[str] = []
        for term, values in self.GOAL_ROOTS.items():
            if term in lower:
                roots.extend(values)
        return tuple(dict.fromkeys(roots or self.DEFAULT_GOAL_ROOTS))

    @staticmethod
    def _unique(items: Iterable[str]) -> List[str]:
        return list(dict.fromkeys(item for item in items if item))

    @staticmethod
    def _risk_rank(risk: str) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(str(risk).lower(), 1)

    def _module_path(self, capability_id: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", capability_id.lower()).strip("_") or "capability"
        return self.MODULE_TEMPLATE.format(capability_id=slug)

    def _graph(
        self,
        specs: Mapping[str, CapabilitySpec],
        roots: Iterable[str],
    ) -> Tuple[CapabilityGraph, List[CapabilitySpec]]:
        """Resolve the goal-relevant dependency graph.

        Undefined dependencies become blocked placeholders instead of being
        silently dropped, cycles are reported instead of raising, and the
        implementation order stays dependency-first and deterministic.
        """
        resolved: Dict[str, CapabilitySpec] = {}
        placeholders: Dict[str, CapabilitySpec] = {}
        edges: Dict[str, List[str]] = {}
        order: List[str] = []
        cycles: List[List[str]] = []
        stack: List[str] = []

        def resolve(capability_id: str, parent: Optional[str]) -> CapabilitySpec:
            spec = specs.get(capability_id) or placeholders.get(capability_id)
            if spec is None:
                spec = CapabilitySpec(
                    capability_id=capability_id,
                    name=capability_id.replace("_", " ").title(),
                    description=f"Referenced by '{parent or 'the goal'}' but absent from the local registry.",
                    status=CapabilityStatus.BLOCKED,
                    implementation="Define this capability before the dependent capability is built.",
                    blocked_reason=f"'{parent or 'the goal'}' depends on an undefined capability.",
                )
                placeholders[capability_id] = spec
            return spec

        def mark_cycle(cycle: List[str]) -> None:
            if cycle not in cycles:
                cycles.append(list(cycle))
            for member in cycle:
                spec = resolve(member, member)
                spec.status = CapabilityStatus.BLOCKED
                spec.blocked_reason = "dependency cycle: " + " -> ".join(cycle)
                resolved.setdefault(member, spec)

        def visit(capability_id: str, parent: Optional[str]) -> None:
            if capability_id in stack:
                mark_cycle([*stack[stack.index(capability_id):], capability_id])
                return
            if capability_id in resolved:
                return
            spec = resolve(capability_id, parent)
            resolved[capability_id] = spec
            edges[capability_id] = list(spec.dependencies)
            stack.append(capability_id)
            for dependency in spec.dependencies:
                visit(dependency, capability_id)
            stack.pop()
            if capability_id not in order:
                order.append(capability_id)

        for root in roots:
            visit(root, None)

        graph = CapabilityGraph(
            nodes=list(resolved),
            edges=edges,
            implementation_order=order,
            statuses={key: spec.status.value for key, spec in resolved.items()},
            blocked=[key for key, spec in resolved.items() if spec.status == CapabilityStatus.BLOCKED],
            cycles=cycles,
        )
        return graph, list(placeholders.values())

    ARCHITECTURE_LAYERS: Tuple[Mapping[str, Any], ...] = (
        {
            "layer": "trigger",
            "capabilities": ("scheduling", "proactive_scheduler"),
            "design": "A durable timezone-aware scheduler emits an approved briefing event.",
        },
        {
            "layer": "context",
            "capabilities": ("context_aggregator", "calendar_integration", "email_integration", "weather"),
            "design": "Provider adapters normalize only consented, redacted summaries into a bounded context object.",
        },
        {
            "layer": "generation",
            "capabilities": ("briefing_generator", "reasoning", "memory"),
            "design": "A deterministic briefing contract calls the existing reasoning boundary with empty-context handling.",
        },
        {
            "layer": "delivery",
            "capabilities": ("notification_layer", "voice_delivery", "voice"),
            "design": "Delivery honors quiet hours, explicit channel consent, deduplication, and offline recovery.",
        },
    )

    @staticmethod
    def _ordered_targets(
        graph: CapabilityGraph,
        partial: Sequence[CapabilitySpec],
        missing: Sequence[CapabilitySpec],
    ) -> List[CapabilitySpec]:
        by_id = {item.capability_id: item for item in [*partial, *missing]}
        return [by_id[item] for item in graph.implementation_order if item in by_id]

    def _architecture(self, relevant: Sequence[CapabilitySpec]) -> List[Dict[str, Any]]:
        present = {item.capability_id for item in relevant}
        layers = [
            {
                "layer": layer["layer"],
                "capabilities": [item for item in layer["capabilities"] if item in present],
                "design": layer["design"],
            }
            for layer in self.ARCHITECTURE_LAYERS
        ]
        return [layer for layer in layers if layer["capabilities"]]

    def _phases(self, targets: Sequence[CapabilitySpec]) -> List[Dict[str, Any]]:
        """Group the roadmap into dependency-ordered engineering phases."""
        target_ids = {item.capability_id for item in targets}
        levels: Dict[str, int] = {}
        for item in targets:
            dependencies = [dependency for dependency in item.dependencies if dependency in target_ids]
            levels[item.capability_id] = 0 if not dependencies else 1 + max(
                levels.get(dependency, 0) for dependency in dependencies
            )
        grouped: Dict[int, List[CapabilitySpec]] = {}
        for item in targets:
            grouped.setdefault(levels[item.capability_id], []).append(item)

        phases: List[Dict[str, Any]] = []
        for level in sorted(grouped):
            source = grouped[level]
            for start in range(0, len(source), self.MAX_PHASE_SIZE):
                batch = source[start:start + self.MAX_PHASE_SIZE]
                phases.append({
                    "phase": len(phases) + 1,
                    "level": level,
                    "name": ", ".join(item.name for item in batch),
                    "capabilities": [item.capability_id for item in batch],
                    "risk": max((item.risk for item in batch), key=self._risk_rank),
                    "permissions": self._unique(item for spec in batch for item in spec.permissions),
                    "tests": self._unique(item for spec in batch for item in spec.tests),
                    "gate": self._phase_gate(batch),
                })
        return phases

    def _phase_gate(self, batch: Sequence[CapabilitySpec]) -> str:
        if any(self._risk_rank(item.risk) >= self._risk_rank("critical") for item in batch):
            return "administrator approval, audit trail, and redaction coverage"
        permissions = self._unique(item for spec in batch for item in spec.permissions)
        if permissions:
            return f"explicit consent for {permissions[0]} plus the phase tests"
        return "existing regression suite stays green and production stays unchanged"

    def _persist(self, plan: CapabilityPlan) -> None:
        """Persist structured capability metadata locally; never source or secrets."""
        if self.store is None:
            return
        try:
            self.store.save_plan(plan)
            self.store.save_registry(plan.registry, goal=plan.goal)
        except OSError as exc:
            plan.record("persistence_warning", "Capability state could not be written locally.", error=str(exc))

    def load(self, analysis_id: str) -> Optional[CapabilityPlan]:
        """Reload a persisted plan so approval state survives a restart."""
        return self.store.load_plan(analysis_id) if self.store is not None else None

    def latest(self) -> Optional[CapabilityPlan]:
        return self.store.latest_plan() if self.store is not None else None

    def analyze(
        self,
        goal: str,
        *,
        snapshot: RepositorySnapshot | None = None,
        roots: Sequence[str] | None = None,
    ) -> CapabilityPlan:
        """Inspect, inventory, gap-analyze, and stop at the approval gate.

        Strictly read-only: no source is edited, no worktree is created, and no
        implementation starts.  The plan is written to the local capability
        store so the approval state survives a restart.
        """
        clean_goal = " ".join(str(goal).split())
        if not clean_goal:
            raise ValueError("Capability analysis requires a goal.")
        self.policy.require(EngineeringOperation.INSPECT_REPOSITORY)
        snapshot = snapshot or self.inspector.inspect()
        baseline: CapabilityAnalysis = self.analyzer.analyze(clean_goal, snapshot)
        registry = self.registry.discover(snapshot)
        by_id = {item.capability_id: item for item in registry}
        graph, placeholders = self._graph(by_id, roots or self._roots_for_goal(clean_goal))
        by_id.update({item.capability_id: item for item in placeholders})
        relevant = [by_id[item] for item in graph.nodes if item in by_id]

        current = [item for item in relevant if item.status == CapabilityStatus.AVAILABLE]
        partial = [item for item in relevant if item.status == CapabilityStatus.PARTIAL]
        planned = [item for item in relevant if item.status == CapabilityStatus.PLANNED]
        missing = [
            item for item in relevant
            if item.status in {CapabilityStatus.MISSING, CapabilityStatus.PLANNED}
        ]
        blocked = [item for item in relevant if item.status == CapabilityStatus.BLOCKED]
        targets = self._ordered_targets(graph, partial, missing)
        dependencies = self._unique(
            dependency
            for item in relevant
            for dependency in item.dependencies
        )
        risks = self._unique([
            *baseline.risks,
            *(
                f"{item.risk} risk capability: {item.name}"
                for item in [*partial, *missing]
                if self._risk_rank(item.risk) >= self._risk_rank("high")
            ),
            *(f"blocked prerequisite: {item.blocked_reason}" for item in blocked if item.blocked_reason),
        ])
        integrations = self._unique(
            integration
            for item in [*partial, *missing]
            for integration in item.integrations
        )
        permissions = self._unique(
            permission
            for item in [*partial, *missing]
            for permission in item.permissions
        )
        architecture = self._architecture(relevant)
        phases = self._phases(targets)
        tests = self._unique([
            "repository inspection remains read-only before approval",
            "dependency ordering is deterministic and cycle free",
            "provider credentials are never logged",
            "production working tree remains unchanged before approval",
            *(test for item in targets for test in item.tests),
        ])
        plan = CapabilityPlan(
            analysis_id=f"capability-{uuid.uuid4().hex[:12]}",
            goal=clean_goal,
            interpretation=(
                f"Build '{clean_goal}' from the existing local JARVIS primitives plus the "
                f"{len(targets)} explicitly identified capability gaps. Nothing is implemented "
                "before explicit approval."
            ),
            status=CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value,
            created_at=utc_now(),
            repository_snapshot={
                "root": snapshot.root,
                "generated_at": snapshot.generated_at,
                "file_count": len(snapshot.files),
                "test_count": len(snapshot.tests),
                "action_count": len({
                    module.tool_name
                    for module in snapshot.python_modules
                    if module.tool_name and module.path.startswith(CapabilityRegistry.DISCOVERABLE_PREFIX)
                }),
                "tracked_file_count": len(snapshot.tracked_files),
                "git_status": list(snapshot.git_status),
                "git_diff_summary": snapshot.git_diff_summary,
                "truncated": snapshot.truncated,
            },
            registry=[*registry, *placeholders],
            current_capabilities=current,
            partial_capabilities=partial,
            missing_capabilities=missing,
            planned_capabilities=planned,
            blocked_capabilities=blocked,
            dependency_graph=graph,
            dependencies=dependencies,
            risks=risks,
            proposed_architecture=architecture,
            required_integrations=integrations,
            required_permissions=permissions,
            estimated_phases=phases,
            test_strategy=tests,
            rollback_strategy=(
                "Each implementation phase runs in a disposable local Git worktree with its own checkpoint; "
                "rollback removes the sandbox or reverts that checkpoint while production stays untouched."
            ),
            deployment_strategy=(
                "No deployment happens during capability analysis. Any later implementation must pass sandbox "
                "tests, verification, and a separate explicit approval before production is touched."
            ),
        )
        plan.record("goal", "Capability-building goal accepted.", goal=clean_goal)
        plan.record("repository_inspection", "Bounded local repository inspection completed.", file_count=len(snapshot.files), test_count=len(snapshot.tests))
        plan.record("capability_registry", "Evidence-backed capability registry generated.", capabilities=len(registry), discovered=sum(1 for item in registry if item.discovered))
        plan.record("capability_graph", "Dependency graph resolved.", nodes=len(graph.nodes), order=graph.implementation_order, cycles=graph.cycles)
        plan.record("gap_analysis", "Existing, partial, missing, planned, and blocked capabilities separated.", existing=len(current), partial=len(partial), missing=len(missing), blocked=len(blocked))
        plan.record("engineering_roadmap", "Engineering phases derived from the dependency order.", phases=len(phases))
        plan.record("capability_approval_required", "Implementation is paused pending explicit capability approval.")
        self._persist(plan)
        return plan

    def _refresh_targets(self, plan: CapabilityPlan) -> None:
        """Re-derive the engineering phases after an operator modification."""
        plan.estimated_phases = self._phases(plan.roadmap_targets())

    @staticmethod
    def _require_approval(plan: CapabilityPlan) -> None:
        if plan.status == CapabilityPlanStatus.CANCELLED.value:
            raise ApprovalRequired("Capability plan was cancelled; no engineering run may start.")
        if not plan.is_approved:
            raise ApprovalRequired("Capability implementation requires explicit approval before it can start.")

    def approve(self, plan: CapabilityPlan, *, approved_by: str = "local_operator") -> CapabilityPlan:
        """Approve the roadmap; only then may the local engineering flow start."""
        if plan.status == CapabilityPlanStatus.CANCELLED.value:
            raise ValueError("A cancelled capability plan cannot be approved.")
        if plan.status == CapabilityPlanStatus.APPROVED.value:
            return plan
        if plan.status != CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value:
            raise ValueError("Capability plan is not awaiting approval.")
        plan.status = CapabilityPlanStatus.APPROVED.value
        plan.approval_required = False
        plan.approved_at = utc_now()
        plan.approved_by = " ".join(str(approved_by or "local_operator").split())[:80]
        plan.record("capability_approved", "Capability roadmap approved for the local engineering flow.", approved_by=plan.approved_by, revision=plan.revision)
        self._persist(plan)
        return plan

    def modify(
        self,
        plan: CapabilityPlan,
        *,
        note: str = "",
        defer_capabilities: Sequence[str] = (),
        add_permissions: Sequence[str] = (),
    ) -> CapabilityPlan:
        """Amend the roadmap and return it to the approval gate.

        Repository analysis is never rewritten: deferral only removes
        capabilities from this revision's engineering phases, and the operator
        decision is recorded for audit.
        """
        if plan.status == CapabilityPlanStatus.CANCELLED.value:
            raise ValueError("A cancelled capability plan cannot be modified.")
        deferred = self._unique([*plan.deferred_capabilities, *defer_capabilities])
        unknown = [item for item in deferred if item not in set(plan.dependency_graph.nodes)]
        if unknown:
            raise ValueError(f"Unknown capability ids cannot be deferred: {', '.join(sorted(unknown))}.")
        plan.modifications.append({
            "timestamp": utc_now(),
            "note": " ".join(str(note).split())[:300],
            "deferred_capabilities": list(defer_capabilities),
            "add_permissions": list(add_permissions),
        })
        plan.deferred_capabilities = deferred
        plan.required_permissions = self._unique([*plan.required_permissions, *add_permissions])
        plan.revision += 1
        if plan.is_approved:
            plan.approved_at = None
            plan.approved_by = None
        plan.status = CapabilityPlanStatus.AWAITING_CAPABILITY_APPROVAL.value
        plan.approval_required = True
        self._refresh_targets(plan)
        plan.record("capability_modified", "Capability roadmap revised and returned to the approval gate.", revision=plan.revision, deferred=list(defer_capabilities))
        self._persist(plan)
        return plan

    def cancel(self, plan: CapabilityPlan, *, reason: str = "") -> CapabilityPlan:
        if plan.status == CapabilityPlanStatus.CANCELLED.value:
            return plan
        plan.status = CapabilityPlanStatus.CANCELLED.value
        plan.approval_required = False
        plan.cancelled_at = utc_now()
        plan.cancelled_reason = " ".join(str(reason).split())[:300]
        plan.record("capability_cancelled", "Capability roadmap cancelled; no implementation was started.", reason=plan.cancelled_reason)
        self._persist(plan)
        return plan

    def submit_decision(
        self,
        plan: CapabilityPlan,
        decision: str | CapabilityDecision,
        **kwargs: Any,
    ) -> CapabilityPlan:
        """Apply the operator decision: APPROVE, MODIFY, or CANCEL."""
        value = decision.value if isinstance(decision, CapabilityDecision) else str(decision).lower()
        try:
            selected = CapabilityDecision(value)
        except ValueError as exc:
            raise ValueError("Decision must be approve, modify, or cancel.") from exc
        if selected is CapabilityDecision.APPROVE:
            return self.approve(plan, **kwargs)
        if selected is CapabilityDecision.MODIFY:
            return self.modify(plan, **kwargs)
        return self.cancel(plan, **kwargs)

    # ----------------------------------------------------------------
    # Self-engineering integration (local Director only)
    # ----------------------------------------------------------------

    def engineering_candidate(self, plan: CapabilityPlan, snapshot: RepositorySnapshot) -> Dict[str, Any]:
        """Translate an approved roadmap into a validated engineering-plan shape.

        Only the plan *shape* is produced here. Implementation content still
        comes from a trusted local implementation callback, never from provider
        text, and every step is medium risk or higher so the Director keeps it
        inside an isolated worktree behind an explicit approval.
        """
        targets = plan.roadmap_targets()
        tracked = set(snapshot.tracked_files)
        # The sandbox is a worktree of committed content, so the verification
        # tests must be tracked files; untracked new tests are not visible there.
        candidate_tests = [path for path in snapshot.tests if not tracked or path in tracked]
        tests = list(candidate_tests[:4])
        step_ids: Dict[str, str] = {}
        steps: List[Dict[str, Any]] = []
        for spec in targets:
            step_id = f"implement-{spec.capability_id}"[:63]
            step_ids[spec.capability_id] = step_id
            risk = spec.risk if self._risk_rank(spec.risk) >= self._risk_rank("medium") else "medium"
            steps.append({
                "id": step_id,
                "description": f"Implement '{spec.name}': {spec.description} {spec.implementation}".strip(),
                "operation": "create_file",
                "files": [self._module_path(spec.capability_id)],
                "dependencies": [step_ids[item] for item in spec.dependencies if item in step_ids],
                "tests": tests,
                "risk": risk,
                "requires_approval": True,
            })
        if not steps:
            return {}
        return {
            "goal": plan.goal,
            "analysis": (
                f"Approved capability roadmap {plan.analysis_id} (revision {plan.revision}) with "
                f"{len(steps)} capability gaps in dependency order."
            ),
            "steps": steps,
            "required_approvals": [self.ENGINEERING_APPROVAL],
            "verification_plan": [
                "compile the changed Python modules",
                "run the targeted capability tests",
                "run the complete existing regression suite",
                "confirm the production working tree is unchanged",
            ],
            "assumptions": [
                "existing JARVIS capability boundaries are reused",
                "no Cloud service, deployment, or remote persistence is required",
                *[f"permission required: {item}" for item in plan.required_permissions],
            ],
            "affected_files": self._unique([
                *[path for step in steps for path in step["files"]],
                *tests,
            ]),
            "proposed_changes": [
                f"Create {step['files'][0]} for the {spec.name} capability."
                for step, spec in zip(steps, targets)
            ],
            "dependencies": list(targets[0].dependencies) if targets else [],
            "tests": tests,
            "risk": max((step["risk"] for step in steps), key=self._risk_rank),
            "rollback_strategy": plan.rollback_strategy,
            "version": 1,
        }

    def execute(
        self,
        plan: CapabilityPlan,
        *,
        director: Any,
        implementation: Optional[Callable[..., Any]] = None,
        repair: Optional[Callable[..., Any]] = None,
        max_repair_attempts: int = 2,
        task_id: Optional[str] = None,
        preserve_sandbox: bool = False,
    ) -> Any:
        """Hand an approved roadmap to the existing local self-engineering Director.

        Refuses to run without approval and refuses to run while the Director is
        disabled. Production is never touched by this method; the Director owns
        the worktree, tests, repair loop, verification, and checkpoint.
        """
        self._require_approval(plan)
        if director is None:
            raise ValueError("An EngineeringDirector is required to run an approved roadmap.")
        if not getattr(director, "enabled", False):
            raise RuntimeError(
                "Self-engineering is disabled; set JARVIS_SELF_ENGINEERING_ENABLED=1 to run an approved roadmap."
            )
        snapshot = self.inspector.inspect()
        candidate = self.engineering_candidate(plan, snapshot)
        if not candidate:
            raise ValueError("The approved roadmap has no capability gaps left to implement.")
        plan.record(
            "engineering_handoff",
            "Approved roadmap handed to the local self-engineering Director.",
            steps=len(candidate["steps"]),
            deferred=list(plan.deferred_capabilities),
        )
        self._persist(plan)
        task = director.execute(
            plan.goal,
            candidate_plan=candidate,
            task_id=task_id or plan.analysis_id,
            approved=True,
            implementation=implementation,
            repair=repair,
            max_repair_attempts=max_repair_attempts,
            preserve_successful_sandbox=preserve_sandbox,
        )
        plan.engineering_task_id = getattr(task, "task_id", None)
        plan.record(
            "engineering_task_created",
            "Local engineering task created from the approved roadmap.",
            task_id=plan.engineering_task_id,
            status=getattr(task, "status", "unknown"),
        )
        self._persist(plan)
        return task

    def report(self, plan: CapabilityPlan) -> Dict[str, Any]:
        """Return a dashboard-safe plan summary without source contents."""
        return plan.to_dict()
