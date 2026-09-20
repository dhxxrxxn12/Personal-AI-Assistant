"""Controlled, opt-in self-engineering primitives for JARVIS.

The package is intentionally isolated from the production task planner.  It
can inspect and plan against a repository without granting an LLM direct
filesystem or shell access.  Mutating operations require an explicit policy
approval and are exposed through small, testable interfaces.
"""

from .capability_analyzer import CapabilityAnalyzer
from .change_memory import ChangeMemory, EngineeringMemoryRecord
from .code_executor import ChangeResult, CodeExecutor
from .deployment_verifier import DeploymentVerifier, VerificationResult
from .director import EngineeringDirector
from .engineering_planner import EngineeringPlanner, PlanValidationError
from .failure_analyzer import FailureAnalysis, FailureAnalyzer
from .git_manager import GitManager
from .models import (
    CapabilityAnalysis,
    EngineeringPlan,
    EngineeringRun,
    EngineeringStep,
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
from .sandbox_manager import SandboxConfig, SandboxManager
from .test_runner import TestResult, TestRunner

__all__ = [
    "ApprovalRequired",
    "CapabilityAnalysis",
    "CapabilityAnalyzer",
    "ChangeMemory",
    "ChangeResult",
    "CodeExecutor",
    "DeploymentVerifier",
    "EngineeringOperation",
    "EngineeringPlan",
    "EngineeringPolicy",
    "EngineeringRun",
    "EngineeringStep",
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
    "SandboxManager",
    "TestResult",
    "TestRunner",
    "EngineeringPlanner",
    "VerificationResult",
]
