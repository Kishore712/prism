# M2.3: Reference Linux Runtime Integration

**Status:** implemented and real cloud acceptance passed 46/46 checks after the recorded r2 failure; M2.3 runtime acceptance is complete, while owner acceptance and `pilot_ready` remain false. The next M2 order 4 named-remote-collaborator stage has begun local implementation under the existing authorization; real OIDC/HTTPS remote validation is not configured. The local image-metadata and KATA_CHECK_ENV fixes are validated. Final cleanup passed `PRISM_CLEAN_FINAL` with `TERMINATED` and zero startup-script metadata keys; final namespace/helpers checks confirmed exact resources absent, and the disk is retained. See the [validation record](validation/m2-linux-runtime.json) and [transcribed cloud-pass evidence](validation/m2-linux-runtime-cloud-pass.json).

M2.3 connects the existing application `Jobs` path to a trusted Linux host worker and Kata runtime for the imported project's fixed `json-check` action. It keeps the approved project version and canonical action identity bound through submission, execution, output, cleanup, and recovery.

## Scope

The operator selects `runtime-profile=reference-linux`. The supported path is:

`Prism application → Jobs → trusted host worker → Kata runtime → bounded result`

Only an approved imported project version and its canonical `json-check` action may enter this path. The worker resolves the approved action identity server-side and does not accept an executable, image, input path, or policy override from model text or an untrusted request.

The runtime must fail closed when the reference profile is unavailable or cannot enforce the approved policy. Docker or `runc` fallback is not allowed. The job has no remote API, SSH, public port, or public listener. Network access remains disabled, source write-back remains denied, and the runtime uses the approved resource limits from M2.2.

M2.3 covers durable job ownership, queued/running/recovered state, cancellation and cleanup, output/result provenance, and restart or worker-loss recovery. Existing Mac development behavior, synthetic records, and prior validation files remain unchanged. Full pilot readiness remains outside this increment: OIDC identities, renewable leases/watchdogs, remote deployment, and the complete pilot release gates are not being implemented here.

## Same-host prerequisites and CLI

The reference profile is a same-host architecture: the Prism application,
trusted controller/worker, containerd, and Kata runtime run on the dedicated
Linux host. The Mac development demo does not call a remote GCP runtime, and a
Mac process cannot satisfy this profile by using a remote API or SSH path.
Cloud-host evidence must run the application and the bounded check on that
trusted host itself, with no public listener.

The host prerequisites are Linux x86_64, a root-owned trusted controller,
cgroups v2, KVM access, and the fixed runtime components Kata 4.2.0, nerdctl
2.3.5, and containerd 2.3.3. The existing dedicated host and its `prism-m0`
containerd namespace are reused; M2.3 does not create a second public service
or adopt resources from another namespace. The runtime still requires its
approved image, handler, policy, and readiness checks before admitting a job.

Run the bounded host check from the repository checkout on that same Linux
host:

```bash
uv run --no-editable python scripts/m2-linux-runtime-check.py
```

For the application path on the same host, start the existing loopback demo
with the imported synthetic acceptance project and explicit profile:

```bash
uv run --no-editable prism demo --data-dir .prism-demo \
  --project "$PWD/examples/document-handoff/.prism-project.json" \
  --runtime-profile reference-linux
```

The development default remains `--runtime-profile development` on Mac. The
reference command above is a bounded local host invocation; it does not itself
establish cloud validation or private-pilot readiness.

The `128 MiB` action memory limit applies to the workload. The Kata VM policy
uses a 512 MiB default and a 1024 MiB maximum. The `32 processes` action bound
is the fixed non-root workload's `RLIMIT_NPROC` boundary; it must not be
described as a guarantee from a Kata guest PID cgroup. These resource meanings
are recorded separately from host VM capacity.

## Recorded cloud attempt and current state

The initial `uv` installation attempt failed because the official `uv 0.11.29`
version string included a target triplet. The r2 attempt allowed the suffix and
then entered real acceptance at `2026-09-21T00:43:09Z`. The dedicated host and
`initial_guest_helpers_absent` checks passed. `execution_completed` failed with
`EngineError`; the attempt recorded `passed=false`, `pilot_ready=false`, and
`model_calls=0`, and the `PRISM_M23_FAILURE` integration check exited 1.

The observed error boundary is `ReferenceLinuxRuntime.__init__` and
`_live_host_checks`; the specific sub-cause is unknown. An r8 diagnosis did not
run because repeated GCP `addmetadata` attempts returned 503 responses and the
service was suspended. CloudShell showed the original account present but an
empty active account; restoring the original logged-in account succeeded, but a
subsequent active-account GET still timed out. That recovery does not establish
a successful cloud acceptance.

The final diagnostic summary was 591 debug bytes with exit 124:
`active_account_missing=false`, `permission_denied=false`,
`service_unavailable=false`, `internal_error=false`, `saw_get_instance=true`,
`saw_post_set_metadata=false`, and `saw_operation_wait=false`. The log stopped
at the pre-write GET and a 90-second timeout. POST and operation-wait were not
observed; their absence is not absolute network proof. Account identifiers and
raw debug were not recorded; the temporary raw log was trapped and removed
(`RAW_LOG_ABSENT`).

The most recently confirmed cleanup state at that diagnostic checkpoint was an
instance `TERMINATED` with no startup script, only the original four keys, and
the disk retained. That observation is historical for the current follow-up.

## Local image-metadata fix and current cloud follow-up

An independent read-only Docker inspect confirmed that `RepoDigests` are
normalized as `python@<fixed digest>`. The reference implementation had
compared the complete `docker.io/library/` string and rejected that normalized
value. This is a confirmed local defect, not evidence of the cloud root cause.

The local helper `_validated_image_metadata` now requires a strict `RepoDigests`
list and member types, exact `@digest` membership, and rejects `Volumes`, while
preserving existing `Id`/`ID` behavior. The earlier local baseline recorded
fourteen focused tests and the cloud agent later reported a full 118-test run;
the final local evidence is twenty-five focused tests and a 129-test run as
recorded below. The r8 diagnostic bundle remains the prior bundle and does not
include this fix; a new fix bundle has not been generated. These results are
local validation and do not establish a real reference-Linux pass.

The current cloud acceptance passed 46/46 checks with `passed=true`,
`pilot_ready=false`, and `model_calls=0`. The revoke path required
`owned_running` and ended as `cancelled_after_active_revoke`, with a trusted
cancelled/null result, fixed error, and exact resource absence. Final namespace
and helper checks passed. `PRISM_CLEAN_FINAL` confirmed `TERMINATED` with zero
startup-script metadata keys; final namespace was empty and exact resources
were absent while retaining the disk. M2.3 runtime acceptance is complete;
owner acceptance and pilot readiness remain pending. The next M2 order 4
named-remote-collaborator stage has begun local implementation, with no claim
of real OIDC/HTTPS remote validation. The complete machine report and probe
hash are transcribed in the cloud-pass evidence record.

An independent read-only Docker inspect also found that the full image name,
`docker.io/library/` name, and short name each exited 1 with `no such image`,
while `images --digests` showed one exact fixed-digest row. This does not prove
the cloud root cause.

The fixed nerdctl, containerd and Kata versions were expected to pass. The
Kata check matrix recorded product `PATH=/usr/local/bin:/usr/bin:/bin` with
`HOME=/nonexistent` as exit 1/not found; adding
`/usr/local/sbin:/usr/sbin:/sbin` with the same HOME as exit 0; using only
`HOME=/root` as exit 0; and using both as exit 0. The minimal local fix applies
`KATA_CHECK_ENV` only to the Kata check, uses the fixed system PATH, does not
inherit private environment, keeps `HOME=/nonexistent`, and leaves other
command environments unchanged. Parent review passed. Twenty-five focused
reference tests, the full 129-test run, and targeted Ruff passed. The
previous RepoDigests normalization fix remains local evidence and is not a
proven cloud root cause. The 4.6 KB diagnostic script hash is
`ea5597810b585f37b716a7cd9b5041d05d52b7cea2742a7406c36f18aacb4e9d`; it was
uploaded and started, but no reliable result was obtained. The r8 bundle still
does not include these fixes.

The r8 cloud/local-startup artifact hash is
`a225e5b8ab7b43788b8aa3faed8b03e4ded01c720ab4f270a57fa415e9595a9b`, and the
bundle hash is
`7cfd8c14199881fab0880c981b8e8ff87178318d75a44efaf4f016d5f044ddeb`.

The complete 46-check machine evidence is transcribed in [the cloud-pass
record](validation/m2-linux-runtime-cloud-pass.json). Its `probe_sha256` is
`8bb6f234a9d9cb3f03d55a5df1fe701e40b943e89508c9d4f08d23aecd4725b9`. The
M2.3 completion-time startup, bundle, reference, cloud-check and test hashes
are recorded under `cloud_acceptance.artifacts` in the validation record. The
`post_cloud_strict_check_sha256` there is a local post-cloud check and is not a
hash of a cloud run.

Historical local fixes retain these boundaries: canonical nerdctl-name inspect
failed, while one exact pinned-digest listing row passed `RepoDigests` and
`noVolumes` inspection; a listing target ID differing from the inspect config
ID is legal. The conflicting `ps -aq --format` form was replaced with
`ps -a --format`. The earlier DB-running-only revoke could terminate before
the handler and leave `uncertain` (43 passed, 1 failed); the current path
requires `owned_running` and uses trusted cancellation. The final script accepts
only `cancelled` for the completed-or-cancelled branch; the cloud run used real
cancellation. Completion-time and post-cloud strict-check hashes are recorded
in the validation record, with the post-cloud check explicitly local rather
than a cloud-run hash.

Local diagnostic changes have 116 tests passed, compile and generated-Bash
checks passed, and product error responses exclude raw stderr. Two leakage
tests are retained as separate boundary evidence. These local results do not
establish a real reference-Linux pass.

### Fail-closed worker-loss rule

The lifecycle distinction is deliberate. A normal worker `SIGTERM` may become
`cancelled` only after that worker confirms cleanup of its owned runtime and
child processes. A forced worker kill or service restart leaves interrupted jobs
`uncertain`, even when the currently known owned resources appear to be gone.
The system must block new work until the operator reviews that state, because an
old worker or detached `nerdctl` process could still create a late resource.
There is no automatic replay or automatic unlock. A short settle interval,
including three seconds, is an observation aid and is not proof that late
creation is impossible. Validation for this boundary is expected to show
cleanup plus `uncertain`/blocked status plus no replay; it is still pending.

## Small Agile backlog

| Slice | Observable outcome | Denial or failure boundary |
| --- | --- | --- |
| Runtime profile admission | `reference-linux` is selected explicitly and reports its supported Kata capabilities before a job starts. | Missing, stale, or unsupported capability blocks the job; no container fallback. |
| Canonical action binding | A reviewed imported version resolves to its approved `json-check` program, image, inputs, and limits. | Forged action/image/input identities, changed digests, unapproved versions, and model-supplied overrides are rejected. |
| Jobs-to-worker dispatch | A job moves through durable queued/running/completed or failed state with owner/session/version binding. | Cross-project, cross-session, duplicate, revoked, and unauthorized requests do not execute or deliver results. |
| Kata execution | The worker runs only the fixed JSON action with network disabled, no host mount, bounded resources, and separate writable scratch. | Readiness or enforcement failure is an explicit unavailable/failed state; the source remains unchanged. |
| Cleanup and recovery | Normal `SIGTERM` reaches `cancelled` only after worker-confirmed cleanup; forced kill or service restart records interrupted work as `uncertain`. | Even when known resources appear cleaned, uncertain work blocks new jobs pending review; late creation cannot be ruled out, and no replay or automatic unlock is allowed. |
| Evidence and readiness | Result records retain approved version/action identities, status, output digest, cleanup state, and runtime profile. | A development result is not labelled as pilot isolation or complete release readiness. |

## Acceptance

Acceptance uses synthetic imported-project data and the fixed `json-check` action on the trusted reference Linux host:

1. A ready `reference-linux` profile admits one approved action and reports the canonical action/image identity.
2. A valid JSON input completes through Jobs → worker → Kata with the M2.2 limits, network denial, no host mount, bounded output, source immutability, cleanup, and provenance recorded.
3. Invalid JSON produces the bounded invalid result without echoing unsafe input; it does not run another action.
4. Invalid profile readiness, changed action/image digest, wrong project/session, revoked version, duplicate request, and unauthorized direct request fail closed.
5. Timeout and normal cancellation reconcile ownership and cleanup before any result is delivered. Forced worker kill, service restart, and runtime-loss paths leave interrupted work `uncertain`, block new jobs pending review, and never replay automatically, even if known resources appear cleaned; a settle interval is not treated as proof of termination.
6. The existing Mac development path and historical records remain unchanged.

The earlier r2 cloud verification failed at `execution_completed`; the later
bounded cloud acceptance passed 46/46 checks, while pilot readiness remains
false. Private-
pilot readiness, OIDC, lease watchdogs, remote SSH/API access, public
networking, arbitrary tools, and broader actions remain outside this
increment's acceptance claim. Local diagnostic tests and generated checks are
not substitutes for real reference-Linux acceptance.
