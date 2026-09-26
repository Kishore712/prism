# M2 Order 5: Owner Decisions on Access Requests

**Status (2026-09-25):** The local implementation is complete and its focused
and full test suites, targeted Ruff check, frontend Prettier check and frontend
production build passed. A focused independent read-only code review found two
issues; both were fixed and re-reviewed, with no further high-severity
findings. This was a static review, not an audit. Owner acceptance is pending.
The current private Linux service is still running an older release; this
branch has not been deployed. The reviewed service updater accepts only exact
`webapp.py`, `app.js`, and `identity.py` patches, while this increment changes
multiple backend modules. An updated, reviewed packaging path is required
before browser acceptance. No browser walkthrough, live identity-provider,
model, or cloud runtime acceptance is claimed. See the
[validation record](validation/m2-access-decisions.json); `pilot_ready` remains
false.

## Reproduce the local checks

From the repository root, rerun the full backend suite:

```bash
uv run --no-editable python -m unittest discover -s tests -q
```

To rebuild the frontend, run this from `frontend/`:

```bash
npm exec -- prettier --check src/main.jsx
npm run build
```

The recorded focused run covered identity, handoff, owner and access-decision
tests (57 passed), but its exact command was not retained. The full suite passed
202 tests. The recorded scope is in the
[validation record](validation/m2-access-decisions.json).

## Owner walkthrough

**Run this walkthrough only after the updated order 5 release has been safely
packaged, reviewed, privately deployed, and its service health checked.** The
currently running private Linux service is older than this branch, so it does
not yet have these owner controls. The existing updater cannot package this
multi-module backend change as-is. No browser or two-identity acceptance has
been completed for this feature yet.

1. Sign in to the private identity-service as the configured owner and open the
   **Requests** section.
2. Review the collaborator's request description, verified recipient issuer
   and subject, and originating session. A request by itself grants nothing.
3. Select an eligible approved, unrevoked version. The server compares the
   immutable project identity, not its display name. Review its frozen digest
   and select a capability supported by that reviewed version:
   **Read and converse** (`inspect`) or **Read, converse and run the reviewed
   action** (`verify`). A `verify` decision is rejected unless the selected
   version explicitly permits its reviewed action.
4. Choose **Approve and create invitation** or **Deny request**. Denial is
   terminal and creates no invitation. Approval displays a new invitation link
   once. Share it privately with the same verified recipient.
5. The recipient redeems that one-use link while signed in with the exact bound
   OIDC identity. Redemption creates a fresh session with the selected version
   and capability. The recipient can refresh request status in the original
   session; an approved status tells them to obtain the separate invitation.

For versions created with exact project provenance, a follow-up may select a
different approved version only when its immutable project identity matches the
source version. Legacy versions without that mapping may only select the exact
originating version; a matching project title is not sufficient. Denial stays
available even when no version is eligible.

The interface sets the follow-up invitation lifetime to five minutes. The
service rejects approvals if the source grant cannot cover that lifetime, if
the source grant or source version has been revoked/expired, if the version is
not separately approved, or if it belongs to another project. The underlying
API accepts lifetimes from five minutes to 24 hours; this is not exposed as an
owner choice in the current interface.

## Security boundary and limits

- A request records a proposal, not authority. It does not widen the current
  session, reveal additional files, run work, or guarantee approval.
- Approval binds a new, single-use invitation to the request's exact verified
  issuer and subject, an approved version with matching immutable project
  provenance (or the exact source version for unmapped legacy data),
  capability/action, and expiry. The owner API rechecks the source session,
  grant revision, version, and identity before creating it. Another identity
  cannot redeem it.
- The original session retains its existing scope. The follow-up session is
  fresh and receives only the selected approved version and capability.
- The invitation can only be redeemed before expiry, once, and while its source
  grant and reviewed version remain valid. Revoking either before redemption
  blocks activation/redemption.
- After redemption, the follow-up grant has a separate lifecycle. Revoking the
  original grant does not revoke an already redeemed follow-up grant; revoke
  that grant separately. Expiry and revocation block subsequent authorized
  access. They cannot retrieve information the recipient already saw or saved.
- Decisions are one-way for a request: a denied request cannot later be
  approved, and an already-decided request cannot be decided again. Concurrent
  decisions allow only one result.
- The feature is limited to the configured named-identity service. It does not
  add automatic scope inference, arbitrary tools/actions, or runtime lifecycle
  guarantees. Local tests do not establish live OIDC, broad security, or pilot
  readiness.

## Acceptance checklist

- As a named recipient, request access and verify the current session still
  cannot read unapproved evidence or use a capability it does not have.
- As owner, deny a request and confirm it is terminal and no invitation is
  created.
- As owner, approve a request using a same-project reviewed version and confirm
  the original session stays unchanged while a separate one-use link appears.
- Redeem as the bound recipient and confirm a fresh session receives only the
  chosen version/capability; try a different identity and confirm denial.
- Confirm an unapproved, revoked, wrong-project, or incompatible-action version
  cannot be approved; a same-title version from a different immutable project
  must also be rejected. Confirm source revocation/expiry blocks an unredeemed
  approval and that a redeemed follow-up grant can be revoked independently.

These acceptance steps should use synthetic data. The current validation
record covers local automated checks and independent review; run and report
browser acceptance, live identity-provider behavior, and cloud runtime checks
as separate evidence classes.
