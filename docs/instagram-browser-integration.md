# Local Instagram browser integration

## Architecture

`AutonomousRuntime` routes Instagram/social goals through `SocialService`.
The service composes `InstagramBrowserProvider`, the existing `SocialAnalyzer`,
and `SocialSummarizer`. `InstagramBrowserProvider` uses the `BrowserSession`
interface in `automation/social/browser_session.py`; its production adapter
delegates navigation and visible DOM reads to the existing
`actions.browser_control` engine. No second browser automation engine is added.

Inbox reads return only visible conversation-list previews and their visible
sender, timestamp, unread marker (when explicitly available), and conversation
link. Message classification and the combined account report remain in the
existing analyzer and summarizer.

## Browser and account setup

The connector uses local persistent browser profiles exposed by the existing
browser-control action. By default, `instagram_main` uses Chrome and
`instagram_secondary` uses Firefox. Both account records can be supplied to
`InstagramBrowserProvider(accounts=...)` with a browser name in the account's
`metadata["browser"]`. These settings contain no credentials.

Install/start the configured browser through JARVIS browser control, open
Instagram, and complete sign-in or verification yourself in that browser.
Then ask JARVIS to check the Instagram inbox. The connector may navigate that
browser to Instagram's Direct inbox, but it does not fill login forms, handle
passwords, or automate verification.

For example:

```python
from automation.social import InstagramBrowserProvider, SocialAccount

accounts = [
    SocialAccount("instagram_main", "instagram", "Main", "MAIN", True, {"browser": "chrome"}),
    SocialAccount("instagram_secondary", "instagram", "Secondary", "SECONDARY", True, {"browser": "firefox"}),
]
provider = InstagramBrowserProvider(accounts=accounts)
```

## Authentication behavior

The provider detects `AUTHENTICATED`, `NOT_AUTHENTICATED`, `LOGIN_REQUIRED`,
`SECURITY_CHALLENGE`, and `UNKNOWN` from the browser URL and visible page text.
Login and security-challenge states stop extraction and report:

> Instagram requires login/verification. Please complete it in the browser.

JARVIS does not solve CAPTCHAs, bypass two-factor authentication, or attempt to
continue through security challenges. If the browser page is ambiguous, data is
reported as unavailable rather than inferred.

## Supported operations

- Read the visible Direct inbox conversation list for configured accounts.
- Normalize visible preview text and available account, conversation, sender,
  timestamp, and unread-state data.
- Analyze and summarize inbox messages with the existing social analyzer and
  summarizer.
- Draft a response locally without sending it.
- Open a visible conversation.
- Mark reviewed or reply only through an explicitly approved service action.
- Verify a reply by checking that its exact text appears in the visible
  conversation after sending.

Notifications, comments, mentions, follower changes, and content metrics are
currently `UNAVAILABLE`. The connector does not claim that these features have
been extracted.

## Permission and duplicate behavior

Inbox reading and analysis are read-only. Drafting a reply does not interact
with Instagram. `REPLY_MESSAGE` requires both an explicit `approved=True`
decision and a permission-manager decision that allows the action. Permission
denial blocks execution.

Before a browser-side effect, the service records a SHA-256 action key and an
`IN_PROGRESS` state in the local file
`~/.jarvis/social_action_ledger.json`. The ledger contains only the hashed
account/conversation/message/action identity and a status, not message text,
credentials, or browser state. A completed, in-progress, or unverified action
cannot be automatically retried. A failed visual verification is routed to the
existing replanner as a non-executing recovery request; it never triggers a
blind resend.

## Security boundaries and limitations

- No Instagram password, token, cookie, or authentication header is accepted
  by the social provider or written to project files or learning memory.
- The connector reads only visible browser elements and uses an allowlist of
  non-secret element attributes.
- It does not issue API requests, scrape hidden/private page state, or bypass
  platform controls.
- Instagram's visible interface can change. If the conversation list cannot
  be reliably recognized, the provider reports it as unavailable.
- A visible unread marker is used only when detected; otherwise unread state
  is `unknown`, not guessed.
- The browser integration tests use fake sessions and never access a real
  Instagram account.

## Troubleshooting

- **Login required:** Sign in yourself in the configured browser and retry.
- **Security challenge:** Complete the requested verification yourself; JARVIS
  stops until you do.
- **Unavailable inbox:** Confirm the configured browser is installed, signed in,
  and showing Instagram's Direct inbox. The UI may have changed or may not
  expose recognizable visible conversation rows.
- **Wrong account:** Assign distinct, correct browser names to `MAIN` and
  `SECONDARY`; each browser session uses its own authorized browser profile.
- **Reply not verified:** Do not repeat the send automatically. Inspect the
  visible conversation manually before deciding what to do next.
