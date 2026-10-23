# Provider-independent telephony adapter boundary

Stage 5B defines the local boundary for a future telephone provider without
connecting one.

`automation.telephony_adapter.TelephonyAdapter` is intentionally narrow. A
provider implementation may report readiness and capabilities, start or end
an approved call, and deliver validated provider events and audio frames. It
must not own persistence, permissions, outgoing approval, or Gemini/audio
device state.

The existing `CallCoordinator` remains the only authority for call state and
approval. Provider event payloads must be parsed and validated before being
passed to `CallCoordinator.event()`. Event IDs are used for idempotency.

The existing `CallAudioBridge` remains the runtime boundary between connected
call state and `AudioOwnershipManager`. A future provider media adapter should
deliver frames to that boundary, use authenticated/replay-protected events,
and stop its media workers before releasing the owner-stop lease.

Before a real adapter is considered, it must additionally provide:

- webhook signature verification and replay protection;
- authenticated event delivery and stable provider/call identifiers;
- destination-specific approval and call-ID binding;
- secret storage outside source control;
- rate limits, fraud controls, and emergency-number protections;
- redacted logs and an explicit audio-retention policy;
- India eligibility, KYC, caller-ID, and media-anchoring confirmation.

`SimulatedTelephonyAdapter` is a deterministic fake only. It opens no sockets,
uses no provider SDK, and does not prove interoperability with any real
telephony service.
