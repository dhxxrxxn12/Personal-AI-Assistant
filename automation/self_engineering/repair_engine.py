"""Bounded test/diagnose/repair/retest orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .failure_analyzer import FailureAnalysis, FailureAnalyzer
from .test_runner import TestResult


@dataclass(frozen=True)
class RepairAttempt:
    number: int
    analysis: FailureAnalysis
    repair_applied: bool
    retest: Optional[TestResult] = None


@dataclass
class RepairReport:
    success: bool
    attempts: List[RepairAttempt] = field(default_factory=list)
    final_result: Optional[TestResult] = None
    stopped_reason: str = ""


class RepairEngine:
    """Run a finite repair loop; the repair callback owns all modifications."""

    def __init__(self, *, max_attempts: int = 2, analyzer: Optional[FailureAnalyzer] = None) -> None:
        if max_attempts < 0:
            raise ValueError("max_attempts must be non-negative")
        self.max_attempts = max_attempts
        self.analyzer = analyzer or FailureAnalyzer()

    def run(
        self,
        test: Callable[[], TestResult],
        repair: Optional[Callable[[FailureAnalysis, int], bool]] = None,
    ) -> RepairReport:
        result = test()
        if result.success:
            return RepairReport(True, final_result=result, stopped_reason="initial validation passed")
        attempts: List[RepairAttempt] = []
        for number in range(1, self.max_attempts + 1):
            analysis = self.analyzer.analyze(result)
            if repair is None:
                return RepairReport(False, attempts, result, "no repair callback was provided")
            try:
                applied = bool(repair(analysis, number))
            except Exception as exc:
                return RepairReport(False, attempts, result, f"repair callback failed: {type(exc).__name__}")
            if not applied:
                attempts.append(RepairAttempt(number, analysis, False, None))
                return RepairReport(False, attempts, result, "repair callback declined to change code")
            result = test()
            attempts.append(RepairAttempt(number, analysis, True, result))
            if result.success:
                return RepairReport(True, attempts, result, "validation passed after repair")
        return RepairReport(False, attempts, result, f"maximum repair attempts ({self.max_attempts}) reached")
