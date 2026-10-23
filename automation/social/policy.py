from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List


SAFE_LEARNING_VALUES = {
    "preferred_response_length": {"brief", "short", "concise", "medium", "detailed"},
    "spam_tolerance": {"strict", "normal", "lenient", "high", "low"},
    "collaboration_priority": {"high", "medium", "low", "first", "normal"},
    "reply_window": {"morning", "afternoon", "evening", "business hours", "anytime"},
    "reply_style": {"concise", "warm", "formal", "casual", "friendly"},
    "preferred_account": {"main", "secondary"},
    "spam_handling": {"ignore", "surface", "review", "summarize"},
    "collaboration_handling": {"prioritize", "review", "summarize", "ignore"},
}


@dataclass
class SocialPolicy:
    """Local policy used to guide social inbox analysis without autonomous execution."""

    require_user_decision_for_reply: bool = True
    max_summary_items: int = 5
    safe_learning_keys: List[str] = field(
        default_factory=lambda: [
            "preferred_response_length",
            "spam_tolerance",
            "collaboration_priority",
            "reply_window",
            "reply_style",
            "preferred_account",
            "spam_handling",
            "collaboration_handling",
        ]
    )

    def is_safe_learning_key(self, key: str) -> bool:
        normalized = (str(key or "")).strip().lower()
        return bool(normalized) and normalized in {item.lower() for item in self.safe_learning_keys}

    def sanitize_learned_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}
        for key, value in (metadata or {}).items():
            normalized_key = str(key).strip().lower()
            if not self.is_safe_learning_key(normalized_key) or not isinstance(value, str):
                continue
            normalized = value.strip().lower()
            allowed_values = SAFE_LEARNING_VALUES.get(normalized_key, set())
            if normalized in allowed_values and not re.search(r"\b[a-z0-9_-]{32,}\b", normalized):
                cleaned[normalized_key] = normalized
        return cleaned
