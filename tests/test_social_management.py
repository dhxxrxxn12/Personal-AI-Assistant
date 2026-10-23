from __future__ import annotations

from automation.autonomous_runtime import AutonomousRuntime
from automation.learning_memory import LearningMemory
from automation.permission_manager import PermissionManager
from automation.social import MockSocialProvider, SocialAnalyzer, SocialService


def test_social_accounts_and_ranking():
    provider = MockSocialProvider()
    accounts = provider.get_accounts()
    assert len(accounts) == 2
    inbox = provider.get_inbox()
    analyzer = SocialAnalyzer()
    normalized = [analyzer.normalize_message(item.to_dict(), account_id=item.account_id) for item in inbox]
    assert any(item.category == "COLLABORATION" for item in normalized)
    assert any(item.category == "QUESTION" for item in normalized)
    assert any(item.category in {"SPAM", "POSSIBLE_SCAM"} for item in normalized)
    assert max(normalized, key=lambda item: item.priority == "HIGH").priority == "HIGH"


def test_social_summary_and_user_decision():
    service = SocialService(provider=MockSocialProvider(), permissions=PermissionManager(), learning_memory=LearningMemory(path=None))
    report = service.summarize_accounts(service.get_accounts())
    assert report["report_title"] == "JARVIS - INSTAGRAM REPORT"
    assert report["requires_user_decision"] is True
    assert report["decision_prompt"] == "What do you want me to do?"
    decision = service.request_user_decision(recommendations=report["recommended_actions"])
    assert decision["requires_user_decision"] is True
    assert "Send it" in decision["choices"]


def test_permission_enforces_social_actions():
    service = SocialService(provider=MockSocialProvider(), permissions=PermissionManager(), learning_memory=LearningMemory(path=None))
    assert service.permissions.check("read_inbox").allowed is True
    assert service.permissions.check("send_reply").requires_confirmation is True
    assert service.execute_action("REPLY_MESSAGE", approved=False)["status"] == "BLOCKED"


def test_social_runtime_routes_instagram_goals():
    runtime = AutonomousRuntime(
        action_registry=None,
        permissions=PermissionManager(),
        learning_memory=LearningMemory(path=None),
        social_provider=MockSocialProvider(),
    )
    result = runtime.run("Check both my Instagram accounts and ask me what to do.")
    assert result["status"] == "COMPLETED"
    assert result["requires_user_decision"] is True
    assert "What do you want me to do?" in str(result["decision_prompt"])


def test_learning_keeps_only_safe_social_metadata():
    service = SocialService(provider=MockSocialProvider(), permissions=PermissionManager(), learning_memory=LearningMemory(path=None))
    assert service.remember_preference("preferred_response_length", "short") is True
    assert service.remember_preference("session_cookie", "abc") is False
    assert service.remember_preference("api_token", "secret") is False
