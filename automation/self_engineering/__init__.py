"""Controlled, opt-in self-engineering primitives for JARVIS.

The package is intentionally isolated from the production task planner.  It
can inspect and plan against a repository without granting an LLM direct
filesystem or shell access.  Mutating operations require an explicit policy
approval and are exposed through small, testable interfaces.
"""

from .capability_analyzer import CapabilityAnalyzer
from .capability_builder import (
    CapabilityBuilder,
    CapabilityDecision,
    CapabilityDefinition,
    CapabilityGraph,
    CapabilityPlan,
    CapabilityPlanStatus,
    CapabilityRegistry,
    CapabilitySpec,
    CapabilityStatus,
)
from .capability_store import CapabilityStore
from .change_memory import ChangeMemory, EngineeringMemoryRecord
from .code_executor import ChangeResult, CodeExecutor
from .deployment_verifier import DeploymentVerifier, VerificationResult
from .diagnostics import DiagnosticReport, collect_diagnostics
from .director import EngineeringDirector
from .engineering_planner import EngineeringPlanner, PlanValidationError
from .failure_analyzer import FailureAnalysis, FailureAnalyzer
from .git_manager import GitManager
from .models import (
    CapabilityAnalysis,
    EngineeringPlan,
    EngineeringRun,
    EngineeringStep,
    EngineeringTask,
    EngineeringTaskStatus,
    RepositorySnapshot,
)
from .policies import (
    ApprovalRequired,
    EngineeringOperation,
    EngineeringPolicy,
    RiskLevel,
)
from .repository_inspector import RepositoryInspector
from .repair_engine import RepairAttempt, RepairEngine, RepairReport
from .sandbox_manager import SandboxConfig, SandboxHandle, SandboxManager
from .test_runner import TestResult, TestRunner

__all__ = [
    "ApprovalRequired",
    "CapabilityAnalysis",
    "CapabilityAnalyzer",
    "CapabilityBuilder",
    "CapabilityDecision",
    "CapabilityDefinition",
    "CapabilityGraph",
    "CapabilityPlan",
    "CapabilityPlanStatus",
    "CapabilityRegistry",
    "CapabilitySpec",
    "CapabilityStatus",
    "CapabilityStore",
    "ChangeMemory",
    "ChangeResult",
    "CodeExecutor",
    "DeploymentVerifier",
    "DiagnosticReport",
    "EngineeringOperation",
    "EngineeringPlan",
    "EngineeringPolicy",
    "EngineeringRun",
    "EngineeringStep",
    "EngineeringTask",
    "EngineeringTaskStatus",
    "EngineeringDirector",
    "EngineeringMemoryRecord",
    "FailureAnalysis",
    "FailureAnalyzer",
    "GitManager",
    "PlanValidationError",
    "RepositoryInspector",
    "RepositorySnapshot",
    "RepairAttempt",
    "RepairEngine",
    "RepairReport",
    "RiskLevel",
    "SandboxConfig",
    "SandboxHandle",
    "SandboxManager",
    "TestResult",
    "TestRunner",
    "EngineeringPlanner",
    "VerificationResult",
    "collect_diagnostics",
]
