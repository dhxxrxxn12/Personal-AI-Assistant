"""Secret-safe classification of test failures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .test_runner import TestResult


@dataclass(frozen=True)
class FailureAnalysis:
    category: str
    summary: str
    likely_files: List[str]
    relevant_output: str


class FailureAnalyzer:
    SECRET_RE = re.compile(
        r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;]+)"
    )
    FILE_RE = re.compile(r"(?:^|\s)([A-Za-z0-9_./-]+\.py)(?::\d+)?")

    @classmethod
    def redact(cls, value: str, *, limit: int = 12_000) -> str:
        redacted = cls.SECRET_RE.sub(r"\1=<redacted>", value or "")
        return redacted[-limit:]

    def analyze(self, result: TestResult) -> FailureAnalysis:
        output = self.redact(result.output)
        lower = output.lower()
        if result.returncode == 124 or "timed out" in lower:
            category = "timeout"
        elif "syntaxerror" in lower or "indentationerror" in lower:
            category = "syntax"
        elif "importerror" in lower or "modulenotfounderror" in lower:
            category = "import"
        elif "assertionerror" in lower or "failed" in lower:
            category = "test_failure"
        else:
            category = "execution"
        files = sorted(dict.fromkeys(match.group(1) for match in self.FILE_RE.finditer(output)))[:20]
        summary = f"{category}: test command exited with status {result.returncode}."
        return FailureAnalysis(category, summary, files, output)
