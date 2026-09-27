# M2 Order 5: Owner Decisions on Access Requests

**Status (2026-09-25):** The M2 order 5 implementation has been installed as an
immutable v4 release on the existing private Linux service. The transfer
archive and complete 29-source release hashes were verified; the service is
active and the private TLS self-check passed. This release stage reran the
205-test full suite and targeted Ruff. Frontend Prettier/build checks passed in
the prior local implementation stage and were not rerun during release. A focused independent
read-only review found two issues; both were fixed and re-reviewed, with no
further high-severity findings. This was a static review, not an audit.
Two-identity browser/API acceptance also passed for bounded synthetic request,
denial, approval, one-use redemption and session-separation paths. The
order-5 approved-request invitation was redeemed by its bound recipient and
replay was denied. Separately, a new direct invitation for the same approved
synthetic version and recipient was rejected when opened under the owner's
different Google identity; the intended recipient then redeemed that same
link. Private HTTPS probes returned 401 for owner state without a session
cookie, 403 for a decision POST with no Origin header due to the same-origin
guard, and 401 for the same-origin decision POST without a session cookie.
These were unauthenticated checks, not an authenticated cross-role
authorization test. No new model call or Kata run was made during this
acceptance; `pilot_ready` remains false. See the [release validation
record](validation/m2-access-decisions-release.json) and the preserved
[local validation record](validation/m2-access-decisions.json).

**Local cross-role regression (2026-09-26):** A later focused test signs in an
owner and two distinct recipients through a deterministic OIDC transport and
uses valid session cookies and CSRF tokens. Recipient calls to owner reads and
revoke, owner calls to recipient reads and access requests, and cross-recipient
session reads and requests are denied. The test confirms the domain tables are
unchanged by the denials and only expected denial audit events are added. Each
recipient first submits a valid request to its own session, proving its CSRF
token can authorize a write. All 22 identity tests and targeted Ruff/format
checks passed. The private HTTPS service loaded in Chrome, and a Google
identity that was not the configured owner was rejected at owner login. A
separately authenticated owner received a sign-in-required response from the
review-state API while owner access remained available.

**Bounded live cross-role follow-up (2026-09-26):** One new one-hour,
inspect-only invitation for the approved three-file synthetic Paired version
was redeemed by the previously identified USC test identity in an independent
Chrome incognito profile. Each identity's own state API returned 200;
cross-role state reads returned sign-in-required. Same-origin JSON POSTs using
each role's valid CSRF token returned 401 against the opposite role's API.
Those POSTs used nonexistent target IDs, so they establish route-level role
denial without attempting to mutate an existing record. The exact new grant
was then revoked; a fresh reviewer page load could no longer enter the shared
workspace. A root-only, read-only Linux database check found both service
units active, integrity `ok`, the latest inspect grant revoked, zero active
grants for that version, 17 runs and 77 model dispatches. The one invitation
and grant remain as records. No model call or run was made. This is bounded
acceptance, not a cross-recipient live test or exhaustive authorization audit;
`pilot_ready` remains false. See the [cross-role validation
record](validation/m2-cross-role-api.json).

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

The initial local implementation checkpoint recorded 57 focused tests and a
202-test full suite; its exact focused command was not retained. During the v4
release stage, the full suite was rerun with 205 passing tests and targeted
Ruff passed. Frontend Prettier/build checks belong to the preceding local
implementation checkpoint and were not rerun during release. See the
[release validation record](validation/m2-access-decisions-release.json) and
preserved [initial validation record](validation/m2-access-decisions.json).

## Owner walkthrough

This walkthrough was completed against the private v4 release. It describes
the bounded acceptance path; use synthetic requests and do not treat it as a
broad security evaluation.

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

The recorded live acceptance also submitted an unauthorized shell/private-file
request and confirmed the owner could deny it without granting access. A
separate read/converse request was approved against the already approved
Document version. Redemption by the bound second identity produced a distinct
inspect-only grant and fresh session; the original verify grant/session stayed
unchanged. Replay of that order-5 decision invitation was denied. In a
separate test, a new direct invitation for the same approved synthetic version
and recipient was opened under the owner's different Google identity and
rejected. The invitation remained valid and the bound recipient then redeemed
that same link. Private HTTPS probes returned 401 for owner state without a
session cookie, 403 for an owner decision POST without an Origin header, and
401 for the same-origin decision POST without a session cookie. The 403 was
the same-origin guard response; none of these API probes used an authenticated
non-owner identity. These checks cover only the recorded synthetic flow and
endpoints. See the [sanitized release
validation record](validation/m2-access-decisions-release.json).

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

These acceptance steps use synthetic data. The records distinguish local
automated checks, static review, private deployment, live identity-provider
behavior and browser/API acceptance. No model call or Kata run was made during
the order 5 live acceptance. Passing these bounded checks does not establish
absolute security or pilot readiness.
