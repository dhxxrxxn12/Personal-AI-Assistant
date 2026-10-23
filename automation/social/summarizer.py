from __future__ import annotations

from typing import Any, Dict, Iterable, List


class SocialSummarizer:
    """Formats social inbox results into a decision-support summary."""

    def summarize_account(self, account_report: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "account_id": account_report.get("account_id"),
            "account_type": account_report.get("account_type", "MAIN"),
            "display_name": account_report.get("display_name"),
            "status": account_report.get("status", "AVAILABLE"),
            "authentication": account_report.get("authentication", "UNKNOWN"),
            "availability_reason": account_report.get("availability_reason", ""),
            "unread_messages": account_report.get("unread_message_count", 0),
            "new_conversations": account_report.get("new_conversations", 0),
            "messages_requiring_attention": account_report.get("messages_requiring_attention", 0),
            "questions": account_report.get("question_count", 0),
            "collaboration_requests": account_report.get("collaboration_count", 0),
            "normal": account_report.get("normal_count", 0),
            "low_priority": account_report.get("low_priority_count", 0),
            "spam": account_report.get("spam_count", 0),
            "low_priority_or_spam": (
                account_report.get("low_priority_count", 0)
                + account_report.get("spam_count", 0)
            ),
            "unread_state_unknown": account_report.get("unread_state_unknown_count", 0),
            "priority": "HIGH" if account_report.get("messages_requiring_attention", 0) else "LOW",
        }

    def summarize_accounts(self, reports: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        list_reports = [self.summarize_account(report) for report in reports]
        recommendations: List[str] = []
        for report in list_reports:
            if report["messages_requiring_attention"]:
                recommendations.append(f"Review {report['messages_requiring_attention']} important conversation(s) on {report['display_name']}.")
            if report["questions"]:
                recommendations.append(f"Answer {report['questions']} unanswered question(s) on {report['display_name']}.")
            if report["collaboration_requests"]:
                recommendations.append(f"Review {report['collaboration_requests']} likely collaboration request(s) on {report['display_name']}.")
            low_priority_or_spam = report["low_priority"] + report["spam"]
            if low_priority_or_spam:
                recommendations.append(f"Review {low_priority_or_spam} low-priority or spam item(s) on {report['display_name']}.")

        if not recommendations:
            recommendations = ["No immediate action is required based on the current local inbox data."]

        account_labels = []
        for report in list_reports:
            label = report.get("account_type", "MAIN")
            if report["status"] != "AVAILABLE":
                account_labels.append(
                    f"{label} ACCOUNT\n-------------\nUNAVAILABLE: "
                    f"{report['availability_reason'] or 'Authorized browser data is unavailable.'}"
                )
                continue
            account_labels.append(
                f"{label} ACCOUNT\n-------------\n"
                f"{report['unread_messages']} unread messages\n"
                f"{report['messages_requiring_attention']} require attention\n"
                f"{report['collaboration_requests']} likely collaboration requests\n"
                f"{report['questions']} questions\n"
                f"{report['normal']} normal\n"
                f"{report['spam']} spam"
            )

        return {
            "report_title": "JARVIS - INSTAGRAM REPORT",
            "accounts": list_reports,
            "account_labels": account_labels,
            "recommended_actions": recommendations,
            "decision_prompt": "What do you want me to do?",
            "requires_user_decision": True,
            "summary_text": "\n\n".join(account_labels + ["RECOMMENDED ACTIONS", "-------------------"] + recommendations),
        }
