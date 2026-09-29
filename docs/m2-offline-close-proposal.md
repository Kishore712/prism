# Proposal: Preserve the Local Inbound Shield During an Offline Close

**Status: Proposed for owner review; awaiting approval. This behavior is not implemented or deployed.**

## Context

The second delayed host relapse was observed after attempts 7 and 8 had each passed a 180-second stability window. On the later read-only check, the Prism service, watchdog, and `tailscaled` were failed; `tailscale0` was absent; the Kata task list was empty; database integrity was `ok`; and pending and uncertain run counts were zero. Journals recorded `PollNetMap ... connection reset by peer`, then a watchdog `category=offline` event, service termination, and emergency termination of `tailscaled`. The sequence is temporal correlation only: the reset is not established as the cause of the watchdog decision or close-path outcome. Manual recovery restored the host to service as a point-in-time observation, and made no model calls or grant changes. See the [sanitized browser-active validation record](validation/m2-browser-active-disconnect.json).

The current strict `Online` requirement is shared by the serve/open readiness check and the close-path restoration check. This proposal separates those decisions. Tailscale documents a control plane that coordinates configuration and a per-device data plane that carries traffic; established connections may continue during coordination-service unavailability. Its client preference called Shields Up blocks incoming Tailscale connections while still allowing the device to be visible and send traffic. These documented behaviors motivate a local close check, but they do not prove the cause of Prism's relapse or guarantee the host's security state. See [Tailscale control and data planes](https://tailscale.com/kb/1508/control-data-planes) and [Manage client preferences](https://tailscale.com/docs/features/client/manage-preferences).

## Proposed behavior

Keep the existing strict `Running`/`Online` and pinned identity/safe-preference requirements for serving, opening the service, and any action that exposes the private HTTPS endpoint.

For close-path verification only, when `Self.Online=false`, accept local protection as restored only if fresh, successful local status and preference reads affirm all of the following:

- the exact pinned device identity is present;
- all required safe preferences match the pinned values; and
- Shields Up is affirmatively enabled.

If every condition is confirmed, the close path may treat inbound shielding as verified and leave `tailscaled` running. If any field is absent, stale, inconsistent, unsafe, or unreadable—or if the local client is unavailable—retain the existing emergency block that terminates `tailscaled`. The close path must not restart Prism services or automatically clear Shields Up. Service recovery and unshielding remain explicit operator actions after strict online and preference verification.

## Tests required before any implementation

1. **Allow close while offline and shielded:** a successful local read reports `Self.Online=false`, exact pinned identity, exact safe preferences, and Shields Up true. Close completes with the shield verified, does not kill `tailscaled`, and leaves the Prism service stopped.
2. **Keep serve/open strict:** the same offline-but-shielded state is rejected by serve/open readiness; no HTTPS listener or service start is permitted.
3. **Deny without affirmative shielding:** Shields Up false, missing, null, malformed, or unreadable triggers the emergency block.
4. **Deny identity or preference drift:** a different/missing pinned identity or any unsafe/mismatched preference triggers the emergency block, even if Shields Up is true.
5. **Deny stale or failed observations:** command failure, timeout, stale sample, parse error, or contradictory status must not be treated as shield confirmation and must trigger the emergency block.
6. **No automatic recovery:** every close outcome leaves Prism stopped; no test path restarts it or clears Shields Up. Only an explicit operator recovery path may do so after strict online checks.
7. **Regression:** preserve existing online close, idle tailnet-down fail-close, service-cgroup drain, and emergency-block tests. Include repeated and concurrent close calls to verify deterministic outcomes and audit categories.

## Expected benefit and residual risk

This split could avoid deliberately killing a locally healthy Tailscale client when its coordination status is offline but fresh local state still confirms the exact device and inbound Shields Up protection. It may reduce avoidable host downtime and preserve local diagnostic capability while Prism remains stopped.

Residual risk remains: a local preference read can be stale, incorrect, or insufficient to establish the effective host firewall state; control-plane loss can prevent new connections or policy updates; and Shields Up only describes incoming Tailscale traffic, not every network path to the host. The fallback therefore remains fail-closed on uncertainty. This proposal does not explain either delayed relapse, establish sustained availability, change serving policy, authorize implementation/deployment, or set `pilot_ready=true`.
