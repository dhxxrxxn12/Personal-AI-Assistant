from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from automation.autonomous_runtime import AutonomousRuntime
from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from automation.social import (
    AuthenticationState,
    InstagramBrowserProvider,
    SocialAccount,
    SocialMessage,
    SocialService,
)
from automation.social.browser_session import BrowserControlSession


def _thread_row(
    *,
    href: str = "/direct/t/thread-1/",
    sender: str = "@brand",
    message_text: str = "Hey, interested in a collaboration?",
    unread: Optional[bool] = True,
) -> Dict[str, Any]:
    return {
        "href": href,
        "text": f"{sender}\n{message_text}\n2h",
        "sender": sender,
        "message_text": message_text,
        "timestamp": "2h",
        "unread": unread,
        "aria_label": "Unread message" if unread else "Read message",
    }


class FakeBrowserSession:
    def __init__(
        self,
        *,
        authentication_state: AuthenticationState = AuthenticationState.AUTHENTICATED,
        elements: Optional[List[Dict[str, Any]]] = None,
        page_text: str = "Messages Primary General",
    ):
        self.authentication_state = authentication_state
        self.url = "https://www.instagram.com/direct/inbox/"
        self.elements = list(elements if elements is not None else [_thread_row()])
        self.text = page_text
        self.opened: List[str] = []
        self.clicked: List[Dict[str, Any]] = []
        self.typed: List[Dict[str, str]] = []
        self.fail_verification = False
        self.mark_read_on_open = False
        self.raise_on_find = False

    def open(self, url: str) -> str:
        self.opened.append(url)
        self.url = url
        if self.mark_read_on_open and "/direct/t/" in url:
            for element in self.elements:
                element["unread"] = False
                element["aria_label"] = "Read message"
        return url

    def current_url(self) -> str:
        return self.url

    def page_text(self) -> str:
        return self.text

    def find_elements(self, selector: str) -> List[Dict[str, Any]]:
        if self.raise_on_find:
            raise RuntimeError("visible elements unavailable")
        return list(self.elements)

    def is_authenticated(self) -> bool:
        return self.authentication_state == AuthenticationState.AUTHENTICATED

    def click(self, *, selector: Optional[str] = None, text: Optional[str] = None) -> str:
        self.clicked.append({"selector": selector or "", "text": text or ""})
        if text == "Send" and not self.fail_verification and self.typed:
            self.text += "\n" + self.typed[-1]["text"]
        return "clicked"

    def type(self, *, selector: str, text: str) -> str:
        self.typed.append({"selector": selector, "text": text})
        return "typed"

    def wait(self, seconds: float) -> None:
        return None

    def screenshot(self) -> str:
        return "fake-screenshot"


def _account(account_id: str = "instagram_main", account_type: str = "MAIN", browser: str = "chrome") -> SocialAccount:
    return SocialAccount(account_id, "instagram", account_type.title(), account_type, True, {"browser": browser})


def _provider(session: FakeBrowserSession, accounts: Optional[List[SocialAccount]] = None) -> InstagramBrowserProvider:
    account_list = accounts or [_account()]
    return InstagramBrowserProvider(
        accounts=account_list,
        session_factory=lambda account: session,
    )


def _permissions(tmp_path: Path) -> PermissionManager:
    return PermissionManager(path=tmp_path / "permissions.json")


def _service(tmp_path: Path, session: FakeBrowserSession) -> SocialService:
    return SocialService(
        provider=_provider(session),
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        action_ledger_path=tmp_path / "action-ledger.json",
    )


def test_authenticated_browser_extracts_visible_inbox():
    provider = _provider(FakeBrowserSession())
    messages = provider.get_inbox("instagram_main")
    assert len(messages) == 1
    assert messages[0].sender == "@brand"
    assert messages[0].text == "Hey, interested in a collaboration?"
    assert messages[0].conversation_id == "thread-1"
    assert provider.get_account_summary("instagram_main")["status"] == "AVAILABLE"


@pytest.mark.parametrize(
    ("auth_state", "expected"),
    [
        (AuthenticationState.NOT_AUTHENTICATED, "NOT_AUTHENTICATED"),
        (AuthenticationState.LOGIN_REQUIRED, "LOGIN_REQUIRED"),
        (AuthenticationState.SECURITY_CHALLENGE, "SECURITY_CHALLENGE"),
    ],
)
def test_authentication_states_stop_inbox_read(auth_state, expected):
    session = FakeBrowserSession(authentication_state=auth_state)
    provider = _provider(session)
    assert provider.get_inbox("instagram_main") == []
    status = provider.get_account_summary("instagram_main")
    assert status["authentication"] == expected
    assert status["status"] == "UNAVAILABLE"
    assert "Please complete it in the browser" in status["reason"] if expected != "NOT_AUTHENTICATED" else True
    assert session.clicked == []
    assert session.typed == []


def test_main_and_secondary_accounts_are_separately_configured():
    provider = _provider(
        FakeBrowserSession(),
        [_account("instagram_main", "MAIN", "chrome"), _account("instagram_secondary", "SECONDARY", "firefox")],
    )
    accounts = provider.get_accounts()
    assert [account.account_type for account in accounts] == ["MAIN", "SECONDARY"]
    assert [account.metadata["browser"] for account in accounts] == ["chrome", "firefox"]


def test_inbox_normalization_preserves_available_unread_state():
    provider = _provider(FakeBrowserSession(elements=[_thread_row(unread=True)]))
    message = provider.get_inbox("instagram_main")[0]
    assert isinstance(message, SocialMessage)
    assert message.unread is True
    assert message.timestamp == "2h"
    assert message.metadata["conversation_url"].startswith("https://www.instagram.com/direct/t/")


def test_unknown_unread_state_is_not_guessed():
    row = _thread_row(unread=None)
    row["aria_label"] = ""
    provider = _provider(FakeBrowserSession(elements=[row]))
    message = provider.get_inbox("instagram_main")[0]
    assert message.unread is None


def test_malformed_visible_inbox_is_unavailable_not_empty_success():
    session = FakeBrowserSession(elements=[], page_text="Welcome to Instagram")
    provider = _provider(session)
    assert provider.get_inbox("instagram_main") == []
    status = provider.get_account_summary("instagram_main")
    assert status["status"] == "UNAVAILABLE"
    assert "recognizable" in status["reason"]


def test_provider_failure_is_explicitly_unavailable():
    session = FakeBrowserSession()
    session.raise_on_find = True
    provider = _provider(session)
    assert provider.get_inbox("instagram_main") == []
    status = provider.get_account_summary("instagram_main")
    assert status["status"] == "UNAVAILABLE"
    assert "visible elements unavailable" in status["reason"]


def test_analyzer_and_service_integrate_normalized_browser_messages(tmp_path):
    service = _service(tmp_path, FakeBrowserSession())
    account = service.get_accounts()[0]
    report = service.analyze_account(account)
    assert report["status"] == "AVAILABLE"
    assert report["collaboration_count"] == 1
    assert report["messages_requiring_attention"] == 1


def test_two_account_summary_does_not_fabricate_unavailable_counts(tmp_path):
    sessions = {
        "instagram_main": FakeBrowserSession(),
        "instagram_secondary": FakeBrowserSession(authentication_state=AuthenticationState.LOGIN_REQUIRED),
    }
    accounts = [_account("instagram_main"), _account("instagram_secondary", "SECONDARY", "firefox")]
    provider = InstagramBrowserProvider(accounts=accounts, session_factory=lambda account: sessions[account.account_id])
    service = SocialService(
        provider=provider,
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        action_ledger_path=tmp_path / "ledger.json",
    )
    summary = service.summarize_accounts()
    assert summary["report_title"] == "JARVIS - INSTAGRAM REPORT"
    assert summary["accounts"][0]["status"] == "AVAILABLE"
    assert summary["accounts"][1]["status"] == "UNAVAILABLE"
    assert "UNAVAILABLE" in summary["summary_text"]
    assert "What do you want me to do?" == summary["decision_prompt"]


def test_login_prompt_is_presented_to_user(tmp_path):
    service = _service(
        tmp_path,
        FakeBrowserSession(authentication_state=AuthenticationState.LOGIN_REQUIRED),
    )
    result = service.summarize_accounts()
    assert "Please complete it in the browser" in result["accounts"][0]["availability_reason"]


def test_runtime_stops_for_login_and_prompts_user(tmp_path):
    session = FakeBrowserSession(authentication_state=AuthenticationState.LOGIN_REQUIRED)
    runtime = AutonomousRuntime(
        action_registry=None,
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        social_provider=_provider(session),
        social_action_ledger_path=tmp_path / "ledger.json",
    )
    result = runtime.run("Check my Instagram inbox")
    assert result["status"] == "AUTHENTICATION_REQUIRED"
    assert result["decision_prompt"] == "Instagram requires login/verification. Please complete it in the browser."
    assert session.clicked == []
    assert session.typed == []


def test_user_decision_flow_requires_user_input(tmp_path):
    service = _service(tmp_path, FakeBrowserSession())
    decision = service.request_user_decision(recommendations=["Review collaboration request"])
    assert decision["requires_user_decision"] is True
    assert decision["question"] == "What do you want me to do?"
    assert "Send it" in decision["choices"]


def test_permission_denial_blocks_reply(tmp_path):
    session = FakeBrowserSession()
    service = _service(tmp_path, session)
    message = service.get_inbox("instagram_main")[0]
    service.permissions.block("send_reply")
    result = service.execute_action("REPLY_MESSAGE", message=message, approved=True, text="Approved reply")
    assert result["status"] == "BLOCKED"
    assert session.clicked == []
    assert session.typed == []


def test_permission_approval_executes_and_verifies_reply(tmp_path):
    session = FakeBrowserSession()
    service = _service(tmp_path, session)
    message = service.get_inbox("instagram_main")[0]
    waiting = service.execute_action("REPLY_MESSAGE", message=message, text="Thanks for reaching out.")
    assert waiting["status"] == "AWAITING_PERMISSION"
    result = service.execute_action(
        "REPLY_MESSAGE",
        message=message,
        approved=True,
        text="Thanks for reaching out.",
    )
    assert result["status"] == "SUCCESS"
    assert result["verified"] is True
    assert session.typed[-1]["text"] == "Thanks for reaching out."
    assert session.clicked[-1]["text"] == "Send"


def test_draft_reply_does_not_touch_browser_or_send(tmp_path):
    session = FakeBrowserSession()
    service = _service(tmp_path, session)
    message = service.get_inbox("instagram_main")[0]
    result = service.execute_action("DRAFT_REPLY", message=message)
    assert result["status"] == "DRAFT"
    assert result["requires_user_decision"] is True
    assert session.clicked == []
    assert session.typed == []


def test_mark_reviewed_requires_confirmation_and_visible_verification(tmp_path):
    session = FakeBrowserSession()
    session.mark_read_on_open = True
    service = _service(tmp_path, session)
    message = service.get_inbox("instagram_main")[0]
    assert service.execute_action("MARK_REVIEWED", message=message)["status"] == "AWAITING_PERMISSION"
    result = service.execute_action("MARK_REVIEWED", message=message, approved=True)
    assert result["status"] == "SUCCESS"
    assert result["verified"] is True


def test_duplicate_send_is_prevented_in_same_service(tmp_path):
    session = FakeBrowserSession()
    service = _service(tmp_path, session)
    message = service.get_inbox("instagram_main")[0]
    first = service.execute_action("REPLY_MESSAGE", message=message, approved=True, text="Thanks!")
    second = service.execute_action("REPLY_MESSAGE", message=message, approved=True, text="Thanks!")
    assert first["status"] == "SUCCESS"
    assert second["status"] == "DUPLICATE_PREVENTED"
    assert len(session.clicked) == 1


def test_verification_failure_is_not_retried(tmp_path):
    session = FakeBrowserSession()
    session.fail_verification = True
    service = _service(tmp_path, session)
    message = service.get_inbox("instagram_main")[0]
    first = service.execute_action("REPLY_MESSAGE", message=message, approved=True, text="Thanks!")
    second = service.execute_action("REPLY_MESSAGE", message=message, approved=True, text="Thanks!")
    assert first["status"] == "VERIFICATION_FAILED"
    assert first["do_not_retry"] is True
    assert second["status"] == "DUPLICATE_PREVENTED"
    assert len(session.clicked) == 1


def test_restart_recovery_keeps_in_progress_action_from_resending(tmp_path):
    ledger = tmp_path / "action-ledger.json"
    session = FakeBrowserSession()
    service = SocialService(
        provider=_provider(session),
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        action_ledger_path=ledger,
    )
    message = service.get_inbox("instagram_main")[0]
    action_key = service._action_key("REPLY_MESSAGE", message)
    service._action_ledger[action_key] = "IN_PROGRESS"
    service._save_action_ledger()

    restarted = SocialService(
        provider=_provider(session),
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        action_ledger_path=ledger,
    )
    result = restarted.execute_action("REPLY_MESSAGE", message=message, approved=True, text="Thanks!")
    assert result["status"] == "DUPLICATE_PREVENTED"
    assert session.clicked == []


def test_learning_accepts_preferences_but_rejects_secrets(tmp_path):
    memory = LearningMemory(path=tmp_path / "learning.json")
    service = SocialService(
        provider=FakeSocialProvider(),
        permissions=_permissions(tmp_path),
        learning_memory=memory,
        action_ledger_path=tmp_path / "ledger.json",
    )
    assert service.remember_preference("preferred_response_length", "concise") is True
    assert service.remember_preference("preferred_response_length", "Bearer abcdef0123456789") is False
    assert service.remember_preference("preferred_response_length", "abc123") is False
    assert service.remember_preference("session_cookie", "not-stored") is False
    stored = (tmp_path / "learning.json").read_text(encoding="utf-8")
    assert "Bearer" not in stored
    assert "not-stored" not in stored


class FakeSocialProvider:
    def get_accounts(self):
        return [_account()]

    def get_inbox(self, account_id=None):
        return [SocialMessage("msg-1", account_id or "instagram_main", "thread-1", "@brand", "2h", "Can we collaborate?", unread=True)]

    def get_account_summary(self, account_id=None):
        return {"status": "OK", "authentication": "AUTHENTICATED"}


def test_action_ledger_persists_only_hashes_and_status(tmp_path):
    service = SocialService(
        provider=FakeSocialProvider(),
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        action_ledger_path=tmp_path / "ledger.json",
    )
    message = service.get_inbox()[0]
    service._action_ledger[service._action_key("REPLY_MESSAGE", message)] = "COMPLETED"
    service._save_action_ledger()
    data = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert list(data.values()) == ["COMPLETED"]
    ledger_text = (tmp_path / "ledger.json").read_text(encoding="utf-8")
    assert "collaborate" not in ledger_text.lower()
    assert "cookie" not in ledger_text.lower()
    assert "token" not in ledger_text.lower()


def test_unsupported_metrics_and_activity_are_explicitly_unavailable():
    provider = _provider(FakeBrowserSession())
    assert provider.get_content_metrics("instagram_main")["status"] == "UNAVAILABLE"
    summary = provider.get_account_summary("instagram_main")
    assert summary["comments_status"] == "UNAVAILABLE"
    assert summary["mentions_status"] == "UNAVAILABLE"
    assert summary["notifications_status"] == "UNAVAILABLE"


def test_browser_control_adapter_never_requests_credentials():
    requests = []

    def fake_action(parameters):
        requests.append(dict(parameters))
        if parameters["action"] == "get_url":
            return "https://www.instagram.com/direct/inbox/"
        if parameters["action"] == "get_text":
            return "Messages Primary General"
        if parameters["action"] == "get_elements":
            return "[]"
        return "ok"

    session = BrowserControlSession(browser_name="chrome", browser_action=fake_action)
    assert session.is_authenticated() is True
    assert all("password" not in request and "cookie" not in request for request in requests)


def test_autonomous_runtime_routes_inbox_goal_and_reply_goal(tmp_path):
    runtime = AutonomousRuntime(
        action_registry=None,
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        social_provider=FakeSocialProvider(),
        state_path=tmp_path / "runtime.json",
    )
    inbox = runtime.run("Check my Instagram inbox")
    assert inbox["summary"]["requires_user_decision"] is True
    reply = runtime.run("Reply to the collaboration request on Instagram")
    assert reply["status"] == "AWAITING_USER_DECISION"
    assert reply["reply_workflow"]["requires_explicit_approval"] is True
    persisted = (tmp_path / "runtime.json").read_text(encoding="utf-8")
    assert "Can we collaborate?" not in persisted


def test_runtime_routes_verification_failure_to_replanner(tmp_path):
    session = FakeBrowserSession()
    session.fail_verification = True
    runtime = AutonomousRuntime(
        action_registry=None,
        permissions=_permissions(tmp_path),
        learning_memory=LearningMemory(path=tmp_path / "learning.json"),
        social_provider=_provider(session),
        social_action_ledger_path=tmp_path / "runtime-ledger.json",
    )
    message = runtime.social_service.get_inbox("instagram_main")[0]
    result = runtime.execute_social_action(
        "REPLY_MESSAGE",
        message=message,
        approved=True,
        text="Thanks!",
    )
    assert result["status"] == "VERIFICATION_FAILED"
    assert result["recovery"]["executed"] is False


def test_security_challenge_never_types_or_clicks():
    session = FakeBrowserSession(authentication_state=AuthenticationState.SECURITY_CHALLENGE)
    provider = _provider(session)
    assert provider.get_inbox("instagram_main") == []
    assert session.clicked == []
    assert session.typed == []
    assert "SECURITY_CHALLENGE" == provider.get_account_summary("instagram_main")["authentication"]
