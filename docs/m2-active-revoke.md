# M2 Order 6: Active Revocation of In-Flight Collaborator Work

**Status (2026-09-25):** The first order 6 increment is installed as immutable
v5 on the private Linux identity-service. An isolated synthetic Linux/Kata
check passed 13/13 checks. A subsequent browser/API end-to-end run with two
verified identities on an approved synthetic Document version independently
observed a Kata job running, then revoked its grant from the same-origin owner
browser poll; the revoke returned HTTP 200 and the exact job was confirmed
cancelled with no result. An earlier browser attempt saw `owned_running`, but
revocation came only after the job had completed, so it remains inconclusive
for active cancellation. Owner acceptance is pending and `pilot_ready=false`.
See the [sanitized live validation record](validation/m2-active-revoke-live.json),
[synthetic validation record](validation/m2-active-revoke.json),
[release guide](m2-recipient-guest-service.md), and the preserved
[order 5 decision guide](m2-access-decisions.md).

## Behavior delivered

The owner can revoke a named collaborator grant while its job is running. The
application records the revoked state, cancels the affected job for that
revoked session, and prevents further reads or submissions through the
revoked session.
The isolated Kata check observed the exact target job in `owned_running`, then
observed its terminal state as `cancelled` with a null result. The live browser
run separately observed `owned_running` on the private Linux host while the
owner's same-origin browser poll reported it active and posted revoke (HTTP
200). The host database confirmed the exact run was `cancelled`, with null
result and an error recording worker exit and cleanup confirmation. The exact
owned resource was absent and the reference namespace count was zero.

Access revocation and execution termination are separate claims. Revoking a
grant denies subsequent authorized access and requests cancellation of its
owned in-flight work. In this tested case, termination was independently
confirmed by the terminal job state, dead worker, and empty Kata namespace.
The result does not establish every possible host failure or termination
timing. A model request already sent to a provider cannot be recalled; this
increment does not claim to cancel a live provider-side model call.

## Exact acceptance observed

On installed v5, the isolated synthetic Linux/Kata script passed all 13 checks:

1. Dedicated Linux host.
2. Initial Kata namespace empty.
3. Reference runtime readiness passed.
4. Exact owned job observed running.
5. Exact runtime resource identity bound to that job.
6. Only the temporary test grant revoked.
7. Subsequent get denied.
8. Subsequent submit denied.
9. Revocation termination observed within the script's 45-second terminal wait bound.
10. Terminal status `cancelled` with no result.
11. Exact runtime resource absent.
12. Final Kata namespace empty.
13. Cleanup confirmed.

The worker was dead after shutdown, no reconciliation was needed, and an
independent post-run Kata resource count was zero. This fake-worker-free Kata
check is separate from tests using synthetic fake workers; those tests do not
prove Kata behavior.

In the later live browser/API run, the owner and a distinct verified recipient
used the approved synthetic Document version. The owner browser poll saw
`owned_running=true` and issued same-origin revoke; HTTP 200 was returned. The
exact Kata run's host record was `cancelled`, result was null, and its error
reported worker exit and confirmed cleanup. The recipient's later GET for the
exact run and GET of `/api/review/state` both returned 403. The grant reached
revoked revision 2 with exactly one revocation event. The exact owned resource
was absent and the reference namespace resource count was zero. The
`model_dispatches` count remained 64. No identities, email addresses, host
names, run identifiers, private paths, or tokens are retained in the live
record. After a forced owner-page refresh, both same-version invitations
showed `Revoked` and the target had no Revoke action; the brief `Active` state
was stale page content. The latest focused suite passed 25/25 checks and
targeted Ruff passed.

The earlier browser attempt is explicitly inconclusive: it observed
`owned_running`, but the revoke happened after completion. It is not evidence
that browser revocation cancelled active work. No new model call was made.

## Repeat the isolated host check

The root-only check is retained on the private Linux host at
`/var/lib/prism/identity-pilot/checks/m2-active-revoke-check.py` with SHA-256
`ab76f3377c95adcbb7eaae3a3eb45aa80dd9e7162cea77899cc92a4eeec87cf2`. Its
imports are pinned to the exact first v5 release directory. Run it only when no
other service work is active: it starts a real Kata job, though its database
and synthetic project are temporary and isolated. From the trusted workstation,
check and connect to the configured private host:

```bash
prism-ssh --check
prism-ssh
```

On the host, run the retained script with the exact first v5 release environment:

```bash
sudo env -i PATH=/usr/local/bin:/usr/bin:/bin LC_ALL=C.UTF-8 PYTHONPATH=/var/lib/prism/identity-pilot/app-releases/service-2764939727afebf7483cbbee647e441a1d958334780f2f0c958c0b6c5a524d0f/src /var/lib/prism/identity-pilot/app-releases/service-2764939727afebf7483cbbee647e441a1d958334780f2f0c958c0b6c5a524d0f/venv/bin/python /var/lib/prism/identity-pilot/checks/m2-active-revoke-check.py
```

The script prints one sanitized JSON result line and exits successfully only
when all 13 required checks pass. The job must reach a running observation
within 20 seconds and termination is polled for up to 45 seconds. The fixed
`json-check` can finish before the script observes the running state; that
produces an inconclusive failure, not a revoke pass. This is an isolated
synthetic runtime check, not browser/OIDC end-to-end acceptance. Do not run
against or point it at the live service database.

## Acceptance boundary and remaining work

The live owner browser revoke path over private HTTPS/OIDC was exercised with
synthetic data. It does not cover cancellation of a live model-provider
request, crash or host-disconnect lease, restarts, streams, or downloads.
Streaming and downloads are not implemented. The 30-second lease target was
not tested. The focused code review was static and independent, found no
remaining blocker after a script cleanup fix, and was not a security audit.
The service remains active on a VM with no automatic stop. Owner acceptance
remains pending; private-pilot readiness remains false.

The immediate next action is to present this bounded evidence for owner review
and resolve feedback. Further order 6 work must cover the remaining lifecycle
conditions and applicable acceptance gates before claiming reliable revocation
across streams, downloads, failures, or restarts.
