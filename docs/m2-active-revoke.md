# M2 Order 6: Active Revocation of In-Flight Collaborator Work

**Status (2026-09-25):** The private Linux identity-service runs immutable v7,
which retains the v5 active-revoke behavior and v6 colocated worker lease, and
adds an independent root host watchdog. The bounded v7 results and limits are
recorded in the [host-watchdog validation record](validation/m2-host-watchdog.json).
At the earlier v5/v6 checkpoints, an isolated synthetic Linux/Kata
check passed 13/13 checks. A subsequent browser/API end-to-end run with two
verified identities on an approved synthetic Document version independently
observed a Kata job running, then revoked its grant from the same-origin owner
browser poll; the revoke returned HTTP 200 and the exact job was confirmed
cancelled with no result. An earlier browser attempt saw `owned_running`, but
revocation came only after the job had completed, so it remains inconclusive
for active cancellation. V6 lease evidence is bounded and its limitations
remain explicit. Owner acceptance is pending and `pilot_ready=false`. See the
[worker-lease validation record](validation/m2-worker-lease.json), [sanitized
live validation record](validation/m2-active-revoke-live.json), [synthetic
validation record](validation/m2-active-revoke.json), [release guide](m2-recipient-guest-service.md),
and the preserved [order 5 decision guide](m2-access-decisions.md).

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

## Colocated worker lease (immutable v6)

The v6 service adds a parent-to-worker liveness lease for the reference-Linux
fixed-job path. After authorization and stop checks, the trusted Jobs parent
renews a pipe while the worker is active. The worker starts its lease monitor
before creating the Kata resource and cancels its owned execution when the
pipe closes, contains malformed data, or stops renewing for the bounded
timeout. Existing runtime cancellation and exact-resource reconciliation then
remove that execution. The parent launches the worker from the pinned release
source even under isolated Python mode; the acceptance check verified that the
worker did not resolve to an older installed package.

The isolated fixed `json-check` submitted through the real v6 Jobs path
completed in Kata, recorded the expected handler and guest kernel, and
confirmed cleanup. A separate test-only Linux/Kata check used the installed
v6 worker lease with a synthetic 12-second sleeper: it observed the exact
owned resource running and its worker/client processes, killed only its own
controller, confirmed the exact resource absent within 30 seconds, observed
the worker and client terminate, saw no resource recreation during a further
five-second observation, and confirmed an empty final namespace. The first
fixed-job controller-loss attempt was inconclusive because the short fixed
action completed before the observer saw it running. A first direct lease
check failed with `EngineError` during pre-observation; its exact cause was not
captured. The subsequent version retries only the specific transient
inspect-during-creation condition and passed 12/12 checks, observing that
condition once. No exact termination latency was recorded beyond absence
within the 30-second deadline. No model calls were made. The test script hash
and sanitized results are in the [validation record](validation/m2-worker-lease.json).
The tested script is retained root-only on the host at
`/var/lib/prism/identity-pilot/checks/m2-worker-lease-runtime-check.py` with
the recorded SHA-256. Its fault injection requires the service to be stopped
and private inbound access shielded; use the recorded evidence for ordinary
acceptance rather than interrupting a live demo.

These checks are complementary, not an end-to-end Jobs-controller crash
proof: the successful lease-loss check directly exercised the installed
worker monitor and Kata runtime with a test-only program. It does not prove
systemd cgroup behavior, remote-host disconnect handling, worker-SIGKILL
handling, or the 30-second bound under stalled runtime operations. A test-only
check observed removal by its 30-second deadline, but the hard target remains
unproven. The lease is colocated with the trusted parent and worker; it is not
an independent control-plane lease. Only Linux/Kata reference execution uses it; development
mode behavior is unchanged. The active service was restored and checked over
private TLS after isolated host testing; active runs were zero, model dispatches
remained 64, the application allowance remained 1,000 cents, and the VM has no
automatic stop.

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
synthetic data. V6 added colocated parent-loss evidence, and v7 adds an
independent host watchdog with bounded worker-SIGKILL and watchdog-crash
probes. These checks do not establish host-disconnect handling, an absolute
30-second guarantee, cancellation of a live model-provider request, or broad
security assurance. Streaming and downloads are not implemented. The
independent high static review found no code-level blocker; it was not a
security audit. The service is active on a VM with no automatic stop. Owner
acceptance remains pending; private-pilot readiness remains false.

At the v6 checkpoint, the immediate next action was to present the v5
active-revoke and v6 worker-lease evidence for owner review. That historical
next-step note is superseded by the v7 checkpoint below; owner review is still
pending. Further order 6 work must cover remaining lifecycle conditions and
applicable acceptance gates before claiming reliable revocation across
failures or restarts.

## Independent root host watchdog (immutable v7)

The immutable v7 release adds a root-owned host watchdog in a separate
systemd cgroup and socket. Before the worker starts Kata, the Jobs controller
registers the exact run identity with the watchdog and waits for its
acknowledgment; the controller renews the watchdog lease. The worker also
maintains a separate colocated pipe lease. Worker death or lease loss causes
the watchdog to kill the identity-service cgroup and reconcile the run's exact
runtime resource; an uncertain or null identity fails closed. If the watchdog crashes,
systemd stops the bound identity unit. A separate watchdog `ExecStopPost`
restores Shields Up before the watcher restarts, so the watcher does not take
the existing tailnet guard down with it.

The normal synthetic fixed `json-check` completed through
`io.containerd.kata.v2`, recorded guest-kernel evidence, cleaned up, and left
the namespace empty. In the worker-death probe, SIGSTOP was sent to the exact
`nerdctl` client and SIGKILL to the exact worker. The exact resource was not
observed before the kill. At +8.05 seconds, the app had failed and stopped, the
run was `uncertain` with a null result, and the exact resource was absent. No
resource reappeared during the next five seconds; the namespace was empty and
Shields Up was true. In the watchdog-crash probe with the exact
client active, at +22.07 seconds the watcher was activating and the app was
deactivating; the run was `uncertain` with a null result, the resource was
absent after the crash, the client was dead, and Shields Up was true. Eight
seconds later the watcher was active, the app inactive, and the namespace
empty. A separate preliminary watchdog-crash probe also began before a runtime
resource was observed. Neither watchdog-crash probe established removal of a
previously observed resource; they establish the reported fail-closed and
service-state observations, not a termination bound.

Three synthetic uncertain rows were resolved manually to `failed`/null only
after inspection confirmed there was no matching Kata resource or process and
Shields Up was true. Resolution used root-only SQLite access; private backups
were retained. This was an inspected operator repair, not an automatic trusted
result or watchdog recovery proof.

The v7 release is immutable. Its transfer archive SHA-256 is
`eb9e409032696fe4b6159cc2d338e548e6c1100d723a45b4b0607549f80bd501`; the
complete 30-source archive SHA-256 is
`1f09da7467b1d76b20ad82ee039e906cd0f18befa359fd851e61d1bf437954dd`. Local
backend checks passed 247/247, targeted Ruff and diff checks passed, and an
independent high static review found no code-level blocker; this was not a
security audit. Final host checks confirmed both units active, private HTTPS
200, Shields Up false in the final active-state check, database integrity
passed, zero uncertain or active runs, and an empty Kata namespace. Three
manual-resolution events were recorded. The model dispatch count is 77, compared with 64 before this
increment; grants total 11, the application model ceiling remains 1,000 cents,
and the VM has no automatic stop.

The probes do not establish host-disconnect behavior, a hard 30-second
termination guarantee, or broad security assurance. No absolute timing SLA is
claimed. Owner acceptance remains pending and `pilot_ready=false`. The
sanitized results are in the [v7 host-watchdog validation](validation/m2-host-watchdog.json).
