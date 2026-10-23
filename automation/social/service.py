from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager

from .analyzer import SocialAnalyzer
from .models import SocialAccount, SocialMessage
from .policy import SocialPolicy
from .browser_provider import InstagramBrowserProvider
from .providers import SocialProvider
from .summarizer import SocialSummarizer


class SocialService:
    """Local decision-support service built on the shared provider and permission APIs."""

    ACTION_RISK = {
        "VIEW_MESSAGE": "READ_ONLY",
        "SUMMARIZE_INBOX": "READ_ONLY",
        "OPEN_CONVERSATION": "READ_ONLY",
        "DRAFT_REPLY": "LOW_RISK",
        "IGNORE_MESSAGE": "LOW_RISK",
        "MARK_REVIEWED": "CONFIRMATION_REQUIRED",
        "REPLY_MESSAGE": "CONFIRMATION_REQUIRED",
    }

    PERMISSION_ACTION = {
        "OPEN_CONVERSATION": "open_conversation",
        "MARK_REVIEWED": "mark_reviewed",
        "REPLY_MESSAGE": "send_reply",
    }

    def __init__(
        self,
        *,
        provider: Optional[SocialProvider] = None,
        permissions: Optional[PermissionManager] = None,
        learning_memory: Optional[LearningMemory] = None,
        action_ledger_path: Optional[Path] = None,
    ):
        self.provider = provider or InstagramBrowserProvider()
        self.permissions = permissions or PermissionManager()
        self.learning_memory = learning_memory or LearningMemory(path=None)
        self.analyzer = SocialAnalyzer()
        self.summarizer = SocialSummarizer()
        self.policy = SocialPolicy()
        self.action_ledger_path = (
            Path(action_ledger_path)
            if action_ledger_path is not None
            else Path.home() / ".jarvis" / "social_action_ledger.json"
        )
        self._action_ledger = self._load_action_ledger()

    def _load_action_ledger(self) -> Dict[str, str]:
        if not self.action_ledger_path.exists():
            return {}
        try:
            raw = json.loads(self.action_ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Social action ledger cannot be read; side effects are blocked for safety.") from exc
        if not isinstance(raw, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw.items()
        ):
            raise RuntimeError("Social action ledger is malformed; side effects are blocked for safety.")
        return raw

    def _save_action_ledger(self) -> None:
        self.action_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.action_ledger_path.with_suffix(self.action_ledger_path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(self._action_ledger, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary_path.replace(self.action_ledger_path)

    @staticmethod
    def _action_key(action_type: str, message: SocialMessage) -> str:
        stable_parts = (
            message.account_id,
            message.conversation_id,
            message.message_id,
            action_type,
        )
        return hashlib.sha256("\0".join(stable_parts).encode("utf-8")).hexdigest()

    def get_accounts(self) -> List[SocialAccount]:
        return list(self.provider.get_accounts())

    def get_inbox(self, account_id: Optional[str] = None) -> List[SocialMessage]:
        return list(self.provider.get_inbox(account_id))

    def normalize_inbox(self, account_id: Optional[str] = None) -> List[SocialMessage]:
        messages = self.get_inbox(account_id)
        return [
            self.analyzer.normalize_message(
                item.to_dict() if hasattr(item, "to_dict") else item,
                account_id=account_id or getattr(item, "account_id", None),
            )
            for item in messages
        ]

    def analyze_account(self, account: SocialAccount) -> Dict[str, Any]:
        messages = self.get_inbox(account.account_id)
        provider_status = self.provider.get_account_summary(account.account_id)
        if provider_status.get("status") != "OK" and provider_status.get("status") != "AVAILABLE":
            return {
                "account_id": account.account_id,
                "platform": account.platform,
                "account_type": account.account_type,
                "display_name": account.display_name,
                "enabled": account.enabled,
                "status": "UNAVAILABLE",
                "availability_reason": provider_status.get("reason") or "Authorized inbox data is unavailable.",
                "authentication": provider_status.get("authentication", "UNKNOWN"),
                "messages": [],
            }
        report = self.analyzer.analyze_account(account, messages)
        report["status"] = "AVAILABLE"
        report["availability_reason"] = ""
        report["authentication"] = provider_status.get("authentication", "AUTHENTICATED")
        return report

    def get_attention_messages(self) -> List[SocialMessage]:
        messages: List[SocialMessage] = []
        for account in self.get_accounts():
            report = self.analyze_account(account)
            messages.extend(
                SocialMessage.from_raw(item)
                for item in report.get("messages", [])
                if item.get("requires_attention")
            )
        return messages

    def summarize_accounts(self, accounts: Optional[Iterable[SocialAccount]] = None) -> Dict[str, Any]:
        selected_accounts = list(accounts) if accounts is not None else self.get_accounts()
        reports = [self.analyze_account(account) for account in selected_accounts]
        return self.summarizer.summarize_accounts(reports)

    def request_user_decision(self, *, recommendations: Optional[List[str]] = None) -> Dict[str, Any]:
        return {
            "requires_user_decision": True,
            "question": "What do you want me to do?",
            "choices": ["Send it", "Edit it", "Don't send", "Ignore", "Remind me later"],
            "recommended_actions": list(recommendations or []),
        }

    def draft_reply(self, message: SocialMessage, text: Optional[str] = None) -> Dict[str, Any]:
        if text is not None:
            draft = str(text).strip()
        elif message.category == "COLLABORATION":
            draft = "Hey! Thanks for reaching out. Could you share more details about the collaboration?"
        elif message.category == "QUESTION":
            draft = "Hey, thanks for your message. I'll take a look and get back to you."
        else:
            draft = "Hey, thanks for reaching out. I'll get back to you soon."
        return {
            "status": "DRAFT",
            "action": "DRAFT_REPLY",
            "message_id": message.message_id,
            "draft": draft,
            "requires_user_decision": True,
        }

    def approve_action(
        self,
        action_name: str,
        *,
        message_id: Optional[str] = None,
        approved: bool = False,
    ) -> Dict[str, Any]:
        decision = self.permissions.check(action_name, {"message_id": message_id})
        if not decision.allowed:
            return {"status": "BLOCKED", "reason": decision.reason}
        if action_name in {"send_reply", "reply_to_message", "instagram_reply"} and not approved:
            return {
                "status": "AWAITING_PERMISSION",
                "reason": "Explicit user approval is required before sending.",
                "action": action_name,
                "message_id": message_id,
            }
        if decision.requires_confirmation and not approved:
            return {"status": "AWAITING_PERMISSION", "reason": decision.reason, "action": action_name}
        return {"status": "APPROVED", "action": action_name, "message_id": message_id}

    def execute_action(
        self,
        action_type: str,
        *,
        message: Optional[SocialMessage] = None,
        approved: bool = False,
        text: Optional[str] = None,
    ) -> Dict[str, Any]:
        action = str(action_type or "").strip().upper()
        risk = self.ACTION_RISK.get(action, "BLOCKED")
        if risk == "BLOCKED":
            return {"status": "BLOCKED", "reason": f"Unsupported social action: {action or 'unknown'}."}
        if action == "DRAFT_REPLY":
            if message is None:
                return {"status": "BLOCKED", "reason": "A message is required to draft a reply."}
            return self.draft_reply(message, text)
        if action == "SUMMARIZE_INBOX":
            return {"status": "SUCCESS", "summary": self.summarize_accounts()}
        if action == "IGNORE_MESSAGE":
            return {"status": "SUCCESS", "action": action, "message_id": getattr(message, "message_id", None)}
        if action == "VIEW_MESSAGE":
            if message is None:
                return {"status": "BLOCKED", "reason": "A message is required to view."}
            return {"status": "SUCCESS", "message": message.to_dict()}
        if message is None:
            return {"status": "BLOCKED", "reason": "A normalized message is required for this action."}

        permission_action = self.PERMISSION_ACTION[action]
        permission = self.permissions.check(permission_action, {"account_id": message.account_id})
        if not permission.allowed:
            return {"status": "BLOCKED", "action": action, "reason": permission.reason}
        if risk == "CONFIRMATION_REQUIRED" and not approved:
            return {
                "status": "AWAITING_PERMISSION",
                "action": action,
                "reason": "Explicit user approval is required before this action.",
                "message_id": message.message_id,
            }
        if permission.requires_confirmation and not approved:
            return {
                "status": "AWAITING_PERMISSION",
                "action": action,
                "reason": permission.reason,
                "message_id": message.message_id,
            }

        action_key = self._action_key(action, message)
        if action_key in self._action_ledger:
            return {
                "status": "DUPLICATE_PREVENTED",
                "action": action,
                "message_id": message.message_id,
                "previous_status": self._action_ledger[action_key],
            }

        executor = getattr(self.provider, "execute_action", None)
        if not callable(executor):
            return {"status": "UNAVAILABLE", "action": action, "reason": "The configured provider cannot execute browser actions."}

        reply_text = text
        if action == "REPLY_MESSAGE":
            if not approved:
                return {"status": "AWAITING_PERMISSION", "action": action, "reason": "The user must explicitly approve sending this reply."}
            if not reply_text or not reply_text.strip():
                return {"status": "BLOCKED", "action": action, "reason": "A non-empty approved reply draft is required."}

        self._action_ledger[action_key] = "IN_PROGRESS"
        self._save_action_ledger()
        result = executor(action, message=message, text=reply_text)
        verified = bool(result.get("verified", result.get("status") == "SUCCESS"))
        if result.get("status") == "DUPLICATE_PREVENTED":
            self._action_ledger.pop(action_key, None)
            self._save_action_ledger()
            return dict(result)
        self._action_ledger[action_key] = "COMPLETED" if verified else "UNVERIFIED"
        self._save_action_ledger()
        if not verified:
            return {
                **result,
                "status": "VERIFICATION_FAILED" if result.get("status") != "DUPLICATE_PREVENTED" else result["status"],
                "do_not_retry": True,
                "message_id": message.message_id,
                "action": action,
            }
        return {**result, "status": "SUCCESS", "message_id": message.message_id, "action": action}

    def remember_preference(self, key: str, value: Any) -> bool:
        if not isinstance(key, str) or not key.strip():
            return False
        normalized_key = key.strip().lower()
        metadata = self.policy.sanitize_learned_metadata({normalized_key: value})
        if not metadata:
            return False
        self.learning_memory.learn(
            name=f"social-{normalized_key}",
            goal="social preference",
            steps=[
                {
                    "action": "record_memory",
                    "parameters": {"key": normalized_key, "value": metadata[normalized_key]},
                    "description": "Store only safe preference metadata.",
                }
            ],
        )
        return True
