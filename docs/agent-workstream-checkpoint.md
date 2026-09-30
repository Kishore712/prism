# Checkpoint Before the Agent Utility Workstream

**Recorded:** 2026-09-29. **Code baseline:** `54e523b447965d844ea7a9e6d8159f655f5c612a`, previously pushed on `fix/tailnet-recovery-guard`. This checkpoint preserves the unfinished M2 status while the owner reviews a more useful agent task. It does not accept M2, start M3 implementation, or establish private-pilot readiness.

## Implemented baseline

- Owner project chat with explicitly selected evidence and private, persisted history.
- Manual selection of files, reviewed conversation excerpts, summaries, and historical results; exact immutable handoff approval.
- Fresh collaborator sessions bound to an approved version and named Google OIDC identity, served over private Tailscale HTTPS.
- Scoped evidence tools and cited answers using the purpose-configured external model route; approved fixed `json-check` execution through Jobs, a separate worker, and the reference Linux/Kata runtime.
- Recipient access requests and owner decisions that issue a separate recipient-bound invitation rather than silently expand an existing session.
- Revocation, a worker lease, independent host watchdog, and guarded handling of uncertain task state. Bounded live checks have exercised active revoke, role denials, and specific fault/cleanup paths; their limits remain in the original records.

Owner and collaborator browsers use distinct authenticated sessions of the Linux application. Permitted actions run in a separate worker and Kata guest. Model inference remains external; Linux execution is not local model inference. Handoff transfers selected application context and copied files, not live process memory or unrestricted owner agent authority.

## Unfinished M2 gate

V8 strict single-attempt Tailnet recovery and the pinned V11 uncertain-run resolver are installed. The [checkpoint](validation/m2-tailnet-guarded-recovery.json) records 125 focused local checks, normal start/manual stop-start, and denial-path validation. It explicitly records `positive_recovery_exercised=false` and `pilot_ready=false`.

The real offline positive recovery path remains unverified. The delayed Tailnet offline events and the browser-active failure retain unresolved causes and unknown execution outcomes where recorded. There is no proven hard shutdown deadline or general uptime/security guarantee. Preserve the [browser-active record](validation/m2-browser-active-disconnect.json) and [recovery proposal](m2-tailnet-recovery-proposal.md), including failures.

The status conversation observed the private owner page returning HTTP 200 on 2026-09-29. This establishes reachability at that check, not continuous health or a new authenticated/model/Kata acceptance. A separate recovery test VM was prepared in the preceding work; its Tailnet enrollment is pending action-time authorization. No enrollment or additional fault test occurred while writing this checkpoint.

## Preservation and development scope

- `docs/live-demo-sop.md` was already modified before this work. It is preserved and excluded from this change.
- Existing grants, conversation history, job results, model reservations, and deployed releases are not changed by this checkpoint.
- The application model allowance remains the previously authorized $10 aggregate reservation ceiling; its current remainder must be read from the service. This is not a provider billing cap.
- No model call, cloud mutation, new dependency, service restart, commit, or push was performed for this documentation increment.
- The owner switched the development session to GPT-6.1 sol. This does not change the Prism application model/provider.

## Proposed next outcome

The owner requested a useful modern-agent task and then asked to define its observable effect before selecting implementation. The [bounded task-completion proposal](agent-task-completion.md) describes a candidate read → edit → validate → return workflow. Its isolated writes and artifact return extend the current Inspect/Verify authority into a narrow Continue capability; that scope requires review before implementation. M2 remains incomplete while this proposal is considered.
