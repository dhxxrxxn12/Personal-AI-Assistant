# Proactive JARVIS analysis

Status: analysis-only. No proactive feature, external connector, phone call,
or production deployment was implemented from this report.

The requested Reel/video file was not present in the workspace attachment. This
analysis therefore uses the written objective: proactive interaction, scheduled
intelligence, personalized briefings, voice interaction, notifications,
calendar/email integration, and eventual phone calling.

## Current architecture mapping

| Capability | Existing evidence | Assessment |
| --- | --- | --- |
| Planning | `automation/autonomous_planner.py`, `automation/autonomous_engine.py`, `cloud/server.py` | Present; plans are action-registry constrained and permission checked |
| Permission boundary | `automation/permission_manager.py`, `cloud/auth.py`, Mac Agent policy path | Present; external or risky actions require confirmation/authentication |
| Task execution | `cloud/server.py`, `agent/mac_agent.py`, `core/action_loader.py`, `actions/` | Present; Cloud queues work for the authenticated Mac Agent |
| Learning/personal memory | `automation/learning_memory.py`, `memory/memory_manager.py` | Present; engineering memory is now separate and strictly keyed |
| Voice | `cloud/voice.py`, `dashboard/static/hud/js/voice.js` | Present; reuse this browser/WebSocket path |
| Notifications | `actions/reminder.py`, `actions/proactive.py` | Partial; notification delivery exists, but durable proactive scheduling is not established |
| Context collection | `actions/background_monitor.py`, `actions/screen_processor.py`, weather/search actions | Partial; connectors and consent boundaries need a dedicated aggregation layer |
| Deployment/observability | `Dockerfile`, `DEPLOYMENT.md`, `cloud/telemetry.py`, dashboard activity stream | Present for Cloud; sandbox verification for autonomous changes is not yet wired |

## Capability inventory and gaps

The deterministic inspector found these current capabilities: planner,
permission manager, learning memory, voice interaction, notifications, context
aggregation, deployment, and tests.

The requested proactive experience needs these missing capabilities:

- Durable proactive scheduler with timezone handling, deduplication, quiet
  hours, opt-in controls, and restart recovery.
- Calendar integration with least-privilege OAuth, event-window selection, and
  redacted context extraction.
- Email integration with explicit account scope, bounded queries, no automatic
  sending, and approval before external communication.
- Telephony integration only after browser voice is reliable; it needs a
  provider, consent/recording policy, caller identity controls, cost limits,
  and explicit approval before every call.

Dependencies identified: durable scheduler, timezone policy, OAuth
credentials, calendar/email API clients, a telephony provider, and a consent
policy. Credentials must remain in the service environment or provider store;
they must never be included in prompts, plans, traces, diffs, or engineering
memory.

## Risk assessment

- Personal data aggregation and privacy exposure from calendar/email/context
  sources.
- Timezone, duplicate-trigger, quiet-hour, and notification-fatigue failures.
- External communication and credential handling.
- Call consent, recording, caller identity, and provider cost risks.
- Production deployment risk; all first implementations belong in a separate
  sandbox and production remains approval-gated.

## Implementation plan

1. Define a scheduler interface and persistent schedule model. Add timezone,
   quiet-hour, opt-in, deduplication, lease, and restart-recovery tests.
2. Add a context-aggregation boundary with provider adapters, strict field
   allowlists, redaction, timeouts, and per-source permissions.
3. Build a briefing generator that consumes a bounded context snapshot and
   returns text/audio-ready content without sending anything externally.
4. Extend the existing browser voice/conversation path for interruption,
   continuity, and briefing follow-up; do not create a second voice system.
5. Add calendar and email adapters behind explicit account permissions. Read
   first; require approval for messages, invitations, or other external writes.
6. Add telephony last, with explicit per-call approval and a hard provider/cost
   boundary. Do not initiate calls from a generic autonomous plan.

## Test plan

Cover scheduler persistence/restart/timezone/deduplication/quiet hours,
context redaction and source failures, briefing determinism, voice reconnect
and interruption, OAuth scope failures, external-write approvals, telephony
consent/cost limits, protected paths, repair limits, sandbox health checks, and
full existing regression coverage. No integration test should send a real
message or place a real call.

## Sandbox deployment plan

Create a task branch and checkpoint the reviewed diff, build a separate
non-production sandbox with no production credentials, run health/API/
functional checks, observe redacted logs, and record the verification result.
Only a human may decide whether a later production deployment is eligible.

The self-engineering director produced this report through the new bounded
inspection and deterministic fallback planner. Because no provider-generated
implementation plan was trusted, the resulting plan was read/test-only.
