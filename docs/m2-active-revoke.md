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

**Uncertain-run resolution host validation (2026-09-26):** The root-only
`inspect`/`resolve` helper was installed separately from immutable v7 on the
existing private Linux host. The installed file was a root-owned, mode-0700,
single-link regular file with SHA-256
`85bb030f23189398f0b7956367c440cc04938670c92ffc7b8fa709a0dfd9a97c`. While
the application was active, production `inspect` correctly denied access
because the shared service lock was occupied and reported no change. After
stopping the app, the production CLI passed the pinned-unit and host prechecks;
an inspect for a nonexistent exact ID was denied as missing/ambiguous. No real
uncertain production row was resolved.

The positive path used an isolated root-private temporary database made from
the complete live schema with one synthetic uncertain reference-Linux row. The
exact installed helper was imported with a test-only database-path override;
real pinned units and real systemd, tailnet, watchdog, namespace, and process
checks still ran. `inspect` returned ready with no change. A harmless Python
process carrying the exact synthetic resource argument caused `resolve` to be
denied. After that process exited, `resolve` changed the synthetic row to
`failed`/null, preserved the original error in the audit event, and reported
the execution outcome as unknown; a repeat resolution was denied. The
temporary database was removed, and the live database SHA-256 was unchanged.

After validation, the service and watchdog were active, the active-state
Shields Up check was false, private HTTPS returned 200, the fixed namespace was
empty, database integrity passed, and live counts remained 12 runs, zero
uncertain/active runs, 11 grants, and 77 model dispatches. No model calls or
demo grants were changed, and the VM remained running. Local validation also
passed 12 focused tests and 259 backend tests, targeted Ruff/diff checks, and
independent high static review with no code-level blocker for bounded host
validation; that review was not a security audit. This establishes a bounded
host run of a synthetic resolution path, not resolution of a real uncertain
production task, broad safety, a hard timing guarantee, owner acceptance, or
pilot readiness. `local-root-operator` remains a generic audit label, not an
authenticated human identity. See the [sanitized validation record](validation/m2-uncertain-resolution.json)
and the [guest service guide](m2-recipient-guest-service.md).

**Observed-resource watchdog host validation (2026-09-26):** The approved
immutable v7 transfer (SHA-256
`eb9e409032696fe4b6159cc2d338e548e6c1100d723a45b4b0607549f80bd501`) was
validated on the existing Linux/Kata host. The first attempt stopped before
Kata creation: `verify_release` rejected a root-owned source directory with
mode 0755. No Kata resource or run was created; both services were restored
and private HTTPS returned 200. The second host execution passed with the
corrected script. A third execution passed using the final formatted script,
installed root-only with SHA-256
`de10d183193d933a059292bff6480ce0bc46b443b3439ce7e24443d80bb18c35`. Its
read-only module, unit-identity, and pidfd preflight passed. It began with
both services stopped, maintenance Shields Up enabled, no active or uncertain
live run, and an empty namespace.

The exact owned Kata resource and client were observed RUNNING before the
fault and still present at expiry. The test renewed the lease, killed only its
owned worker, and proved worker-loss grace. The real v7 watchdog expiry and
resource reconciliation then produced `uncertain`/null in an isolated
synthetic database; the exact client and resource disappeared within the
test's 30-second poll, were not recreated, and the namespace was empty at
confirmed cleanup. Only the service-cgroup kill boundary was replaced, using
pidfd termination of the exact test client. The test made no live database,
grant, or model calls; evidence was not retained.

Afterward the live database was unchanged and passed integrity checks; counts
remained 12 runs, zero active/uncertain runs, 11 grants, and 77 model
dispatches. Both services were active, Shields Up was false while serving,
private HTTPS returned 200, the namespace was empty, and the VM remained
running. Focused tests passed 12/12, existing watchdog tests 15/15, and
targeted Ruff, format, and diff checks passed. This does not establish installed
systemd/service-cgroup end-to-end behavior, a watchdog-triggered Shields Up
transition, host-disconnect behavior, or a hard 30-second SLA. Broader order 6
owner acceptance remains pending and `pilot_ready=false`; use the [sanitized
observed-watchdog record](validation/m2-observed-watchdog.json).

**Installed service-cgroup host validation (2026-09-26):** A separate root-only
maintenance check used the pinned immutable v7 source on the existing private
Linux/Kata host. Preflight confirmed the installed unit identities, both
services active, zero queued/running/uncertain live runs, an empty fixed Kata
namespace, and no other process in the service cgroup. The check enabled
Shields Up, created a temporary synthetic SQLite run, and moved only its test
worker into the real identity-service cgroup before allowing it to start Kata.
It observed the exact owned resource and client RUNNING in that cgroup before
fault injection. The pinned v7 watchdog algorithm was called directly from a
separate maintenance process with its real systemd service-kill function. After
worker loss and grace, it killed the real service cgroup, terminated the
service main process and exact client, and reconciled the resource within the
test's 30-second window. The isolated row became `uncertain` with a null
result; no late recreation occurred, and the namespace and cgroup drained.

The check then restored the service. An independent read-only postcheck found
both units active, Shields Up false while serving, private HTTPS 200, an empty
namespace, only the service main process in its cgroup, and unchanged live
counts: 12 runs, zero queued/running/uncertain, 11 grants, and 77 model
dispatches. The check directly wrote no live database rows and made no model
calls. Its final root-only script SHA-256 is
`ad47761d678762d4ce036e5be402348ac7de83464ab2e3574fb024afe406b2e8`.
Focused local checks passed 31/31 with targeted Ruff and format checks. This
does not test the installed watchdog socket or authenticated Jobs submission:
the real v7 algorithm was invoked directly with an isolated row. Shields Up
was enabled before fault injection, so a watchdog-triggered Shields Up
transition was not established. The 30-second observation is not a hard SLA;
host disconnect and broader order 6 acceptance remain pending. See the
[sanitized service-cgroup record](validation/m2-systemd-cgroup.json).

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

## Local handling of an uncertain reference-Linux row

The watchdog probes recorded above can leave an exact run in `uncertain` with
no result. The separately installed helper offers two root-only actions for
one exact 32-character hexadecimal run ID. Get that ID from the exact task
details or host audit record. Stop the identity service and confirm the
required host state before invoking the CLI:

```bash
sudo python3 /usr/local/libexec/prism-identity-uncertain-resolution.py inspect <32-character-run-id>
sudo python3 /usr/local/libexec/prism-identity-uncertain-resolution.py resolve <32-character-run-id>
```

The installed host CLI is version-specific to the pinned v7 unit/release
identity and is separate from immutable v7. Its production lock-denial checks
used this installed copy. The repository source script is used for local
development and its focused tests only.

`inspect` checks readiness and makes no database change. `resolve` repeats the
checks inside a transaction, then changes only the exact uniquely bound
`reference-linux` row from `uncertain` to `failed`, keeps `result` null, and
commits an audit event that preserves the original `finished`, `error`, and
`result` values. Its replacement error says the execution outcome is unknown;
neither the output nor the state means that the action succeeded.

Both actions require the stopped identity service with no service processes,
Tailscale Shields Up confirmed with unsafe route/SSH preferences disabled, the
independent root watchdog active and healthy, and an unoccupied private
root-owned `server.lock` shared with the service. The stopped service unit and
installed immutable release must also match the pinned v7 identity. The fixed
private database must contain exactly one matching row and no queued or
running work. The row must have status `uncertain`, runtime profile
`reference-linux`, its exact `prism-m23-run-<run-id>` resource, a unique valid
runtime token, and a null result. The fixed `prism-m0` containerd namespace
must be empty, and `/proc` must contain no process whose command line matches
that exact resource or token. Host checks are repeated around the inspection
to reduce state-change races. Any failed check denies the operation without
changing the run or audit log; an audit insert failure rolls back the row
update. The event's `local-root-operator` actor value is a fixed label and does
not establish which human operated the host.

The bounded host validation above used a test-only database override for the
isolated synthetic row; the production database was never mutated. The helper
does not stop or start services, remove runtime resources, or establish that a
real uncertain action succeeded. See the [sanitized validation record](validation/m2-uncertain-resolution.json).

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
