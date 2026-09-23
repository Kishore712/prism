# M0 Host Diagnostics and Synthetic Runtime Validation

**Status, 2026-09-17:** M0's architecture spike and reference-host boundary checks are complete for the recorded configurations. The development runtime passed 29 checks, and the reference Linux/Kata spike passed 33 checks across 10 separate guest boots. This establishes bounded synthetic feasibility, not private-pilot readiness. No collaborator access is enabled. See the [development evidence](validation/m0-development.json), [Linux evidence](validation/m0-linux-kata.json), and [runtime decision](decisions/0001-m0-runtime.md).

This stage provides a read-only diagnostic command, explicit preparation of one pinned public image, and real constrained execution of built-in synthetic probes. It does not yet provide a browser UI, share creation, accounts, grants, an agent/model connection, or arbitrary project execution. A passing synthetic selftest is not a sandbox-escape audit or a privacy guarantee.

## Setup

Requirements: Python 3.13 or newer, uv, and an owner-authorized local Linux Docker engine supporting API 1.47, cgroups v2, and seccomp. Docker Desktop on the tested Apple Silicon Mac supplies the development engine. No Python runtime dependencies are required; the pinned build backend is `uv_build==0.11.29`.

From the repository root:

```bash
uv sync --python 3.13 --no-editable
uv run --no-editable prism doctor
uv run --no-editable prism runtime prepare
uv run --no-editable prism selftest
```

Start the local Docker application or service separately if needed. `doctor` never starts it, downloads images, or modifies the host. `runtime prepare` explicitly downloads the fixed public Python image, if absent; it does not start a job or build from a private project. The host-side download needs registry access, while task containers remain offline.

Use `--no-editable` consistently with uv. The tested macOS environment marks generated editable `.pth` files hidden, which causes Python to ignore them on subsequent launches. A regular local package install avoids that environment-specific issue. Source changes are included in the project's uv cache keys. No global Python configuration is changed.

The Engine API client uses only an explicit local Unix socket: the standard Docker Desktop socket on macOS or `/var/run/docker.sock` on Linux. Override it with `--socket /absolute/local/socket` on a command. It does not read Docker contexts, registry credentials, personal SSH configuration, or `DOCKER_HOST`, and rejects remote URLs. Access to this socket is a trusted host-management capability; do not expose the CLI or socket to collaborators.

## Acceptance steps

1. Run `uv run --no-editable prism doctor --json`. With the pinned image prepared, expect `prerequisites_ready: true` and `pilot_ready: false`. The isolation check remains `unverified`: observing prerequisites is distinct from executing tests.
2. Run `uv run --no-editable prism selftest --json`. Expect `passed: true`, all recorded checks passing, and `pilot_ready: false`. Progress goes to stderr; JSON goes to stdout. This is fresh execution, not playback of the saved report.
3. Run `uv run --no-editable prism doctor --socket /tmp/prism-does-not-exist.sock --json`. Expect a bounded unavailable result and exit code 2, without a traceback or private path disclosure in diagnostics.
4. Run `uv run --no-editable prism selftest --profile pilot --json`. Expect exit code 2, `passed: false`, and an explicit unsupported-pilot message. No development container is started as a fallback.
5. Run the regression checks:

```bash
uv run --no-editable python -m unittest discover -s tests -v
```

Exit codes: 0 means the requested development operation/check succeeded; 1 means a completed selftest observed a failed check; 2 means an unavailable prerequisite, unsupported profile, or invalid input; 130 means interruption. None of these values certify readiness for a private pilot.

## What actually runs

The reviewed fixture is [probe.py](../src/prism/fixtures/probe.py). It contains eight synthetic metric differences and a seeded bootstrap calculation, plus bounded adversarial probes. Fixed seeds are run inside separate real containers and their results are compared. No source project is mounted, no owner agent is attached, and no model service is contacted.

The fixture image is pinned by its multi-platform repository digest in [engine.py](../src/prism/engine.py). The report records the resolved platform image ID, probe-program digest, environment versions, results, and run outcomes. The original public tag used to select it was `python:3.13-alpine`; execution does not follow that mutable tag. The image is for synthetic development and has not received a vulnerability audit or pilot approval.

Per-container constraints:

| Resource | Implemented development constraint |
| --- | --- |
| Identity and privilege | UID/GID 65534, all capabilities dropped, no new privileges, explicit builtin seccomp |
| Filesystem | Read-only root, no host mounts, no implicit image volumes |
| Writable data | Separate 8 MiB `/scratch` tmpfs, `noexec,nosuid,nodev`; private IPC without shared-memory storage |
| Network | Disabled and `none` network mode; no published ports |
| CPU / memory | 0.5 CPU quota, 128 MiB memory, swap disabled |
| Processes | Maximum 32; bounded process-exhaustion probe |
| Output | At most 64 KiB retained by the collector; task stopped when the limit is reached |
| Daemon logs | Local driver, one approximately 1 MiB rotating file, compression explicitly disabled; this is log-driver rotation, not an exact total host-disk quota |
| Time / cancellation | Controller-enforced deadline, kill, observed stopped state, and confirmed container removal |

The real checks cover normal execution, repeated and changed parameters, non-root identity, seccomp, filesystem and environment canaries, missing control sockets, disconnected networking, writable scratch and its ceiling, separate scratch spaces, cgroup limits, process exhaustion, OOM termination, output flooding, timeout/cancellation after observing a child process, unchanged host canary, and cleanup. The CPU check observes the applied cgroup quota; it is not a performance or fairness benchmark. Network probes cannot establish denial for every possible attack.

Host canary files and an environment value are created only for the test and removed/restored afterward. Reports omit raw logs, source paths, credentials, personal files, and hostnames. Results are written only to stdout unless the operator explicitly redirects them. The repository JSON is synthetic validation evidence, not application telemetry.

## Findings from the inspected hosts

- The tested development engine is Docker Desktop 27.4.0, Linux/arm64, API 1.47, cgroups v2. Its default seccomp policy was `unconfined`. The first test detected this; Prism now requests `seccomp=builtin` per container and verifies the effective kernel state. No global Docker setting was changed.
- This kernel creates inactive tunnel devices in otherwise disconnected network namespaces. The probe checks that no non-loopback interface is active and verifies unreachable external/metadata destinations, rather than assuming that only one interface name can exist.
- A user-authorized QEMU guest is Ubuntu 25.04 / Linux 6.14.0-37-generic on arm64 with cgroups v2. Read-only inspection found no `/dev/kvm` and no installed Docker, containerd, or Kata command. No guest packages or configuration were changed.
- A separate paused, diskless capability check with the installed QEMU 10.1.0 and HVF rejected `virtualization=on`: HVF cannot provide the virtualization extensions with that tested QEMU build. The existing guest was neither restarted nor modified. This finding does not establish limitations of all newer QEMU builds, Apple virtualization software, or Linux servers.

## Reference Linux acceptance

The owner-authorized GCP N2 host ran Ubuntu 24.04 LTS / host kernel `7.0.0-1011-gcp`, containerd 2.3.3, nerdctl 2.3.5, and Kata 4.2.0. Each of 10 runs observed guest kernel `6.18.35` and a different guest boot ID. The full test took 46.081 seconds, including guest creation, fixed work, and cleanup. This single sequence is a functional observation, not a latency benchmark.

Review [the Linux JSON](validation/m0-linux-kata.json): require `passed: true`, `pilot_ready: false`, 33 passing checks, ten cleaned runs, distinct guest boot IDs, output retained at or below 65,536 bytes, and `guest_helpers_removed: pass` with an empty remaining-process list. Memory exhaustion exited with 137 without a controller kill; output overflow and both controller-triggered interruption paths also exited with 137 and confirmed cleanup. The interruption probes trigger one second after observing a parent/child workload; they are real termination tests, not implemented user-facing revocation.

The Linux launcher explicitly selects Kata and does not use the CLI's development Docker adapter. Its process ceiling uses guest-kernel `RLIMIT_NPROC=32:32` because this Kata version strips the PID-cgroup field before guest creation. The probe confirms the hard limit cannot be raised and finite forking reaches it. See [cloud setup, findings, and replay instructions](gcp-m0-host.md). A private pilot still requires an integrated executor and the broader product release gates; do not expose these privileged host tools to collaborators.

The Docker and Kata checks are real executions with synthetic data. The Python regression suite tests handling with fake runtime responses where appropriate; it does not substitute for either real runtime report. There is no model call, simulated agent answer, or browser sharing interface in M0.

## Remaining product gates and next stage

The production Linux/Kata adapter, recipient/session lifecycle, host lease loss, worker-crash recovery, OIDC, grants, aggregate budgets, persistent artifact quotas, and real model interaction are not implemented or validated. `pilot_ready` is deliberately always false in M0 tools. Each synthetic run has its own VM; this is not yet a recipient-bound persistent session implementation.

In particular, controller deadlines depend on the controller staying alive. An abrupt controller failure can leave a synthetic task until it exits or is explicitly removed. The test programs have finite work/sleep bounds, but this does not implement a trusted expiring host lease. Normal errors and interruption attempt scoped cleanup; if cleanup cannot be confirmed, the CLI reports the exact generated container name. Inspect that named resource and remove it with the host's container tooling. Never use a global prune command or delete unrelated resources.

The existing local QEMU guest remains a Linux development candidate, not a validated nested Kata host. Actual VM-backed tests on the authorized GCP host close the M0 host-feasibility gate for that recorded configuration only.

**Subsequent host authorization, 2026-09-17:** The owner authorized using existing Google Cloud trial credit to create an appropriately sized Linux test host. An Intel N2 VM with 2 vCPUs, 8 GiB RAM, Ubuntu LTS, explicit nested virtualization, and a four-hour automatic stop was created. Its KVM preflight and Kata guest boundary checks passed. See the [cloud host record](gcp-m0-host.md) for lifecycle status and retained resources. Account upgrades, public application deployment, and private project-data uploads remain outside this scope.

The proposed next milestone is M1's synthetic local sharing path: explicit materialization and exact-content review, a fresh collaborator agent, evidence chat, and one typed runnable. M1 has not started and requires the owner's next milestone confirmation. It must not enable external/private-data use or change the pilot isolation requirements.
