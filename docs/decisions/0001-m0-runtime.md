# ADR 0001: Separate Synthetic Development Execution from Pilot Isolation

- **Date:** 2026-09-17.
- **Status:** Synthetic development and reference Linux/Kata feasibility validated on the recorded hosts; product executor integration remains future work.
- **Scope:** M0 diagnostics and fixed synthetic probes only.

## Context

Prism begins on an Apple Silicon macOS development machine. The product's private-pilot scope calls for a validated Linux VM-backed execution environment and model-independent enforcement. A shared Docker Desktop Linux VM does not establish a separate guest-kernel boundary for every collaborator.

The user has authorized phased implementation with reporting at major milestones, provided an existing QEMU Linux guest for inspection, and indicated that a Linux server may become available later. Inspection established that the current guest lacks `/dev/kvm`; the installed QEMU/HVF build rejects exposing virtualization extensions. No guest-system installation or disk modification is justified by a successful local container demo.

## Decision

1. Implement a small Python CLI with no third-party runtime dependency. Use the Docker Engine API over a local Unix socket for read-only diagnostics, explicit download of one digest-pinned public fixture image, and fixed synthetic tests. Never load Docker credential configuration or accept a remote endpoint.
2. Use structured container specifications with no host mounts, no inherited credentials, no network, finite resources, and per-container builtin seccomp. Run only the packaged reviewed probe program and its bounded parameters. Do not provide a general execution interface.
3. Treat Docker Desktop or a local Linux Docker engine as **development-only**. Always report `pilot_ready: false`; unsupported pilot execution must fail without fallback.
4. Select containerd/Kata on the validated Linux host as the reference direction for later integration. M0's standalone fixed-workload spike verifies guest separation and the documented enforcement mechanisms; a production adapter and broader release gates remain unfinished. Revisit a material isolation change with the user before implementing it.
5. Retain the proposed FastAPI/Pydantic/React and PydanticAI direction for later approved stages. Do not install those dependencies in M0, whose CLI and probes do not need them.

## Threat assumptions and controls

| Concern | M0 control | Limit of the evidence |
| --- | --- | --- |
| Unexpected host-data access | No host mounts; synthetic file/environment canaries | Tests do not prove kernel/runtime escape resistance |
| Prompt or parameter becoming a command | Fixed probe selection, exact typed seed, no shell interpolation | No conversational agent is connected yet |
| Dangerous engine defaults | Explicit seccomp, dropped capabilities, non-root, read-only root and `none` networking | Engine and OS are trusted; behavior is tested on the recorded version |
| Resource exhaustion | cgroup settings, scratch ceiling, output collector and controller deadline | Aggregate/multi-recipient budgets and crash-resistant leases are later work |
| Cleanup damages another task | Random owned names and matching labels; remove only resources created by this runtime | Malicious administrators with the same engine access are outside this boundary |
| Misleading readiness | Prerequisite observations, real tests, and pilot eligibility are separate | M0 reference-host checks pass; broader pilot eligibility remains false |

## Evidence and compatibility

See the [M0 guide](../m0-host-validation.md), [development result](../validation/m0-development.json), and [Linux/Kata result](../validation/m0-linux-kata.json). Regression tests using fake engine responses validate application handling only. Real runtime probes establish observed behavior on the two recorded configurations.

The reference spike uses Kata 4.2.0, containerd 2.3.3, and nerdctl 2.3.5 on an authorized N2 host with nested KVM. Tests found that this Kata version omits guest PID-cgroup limits. An explicit non-root `RLIMIT_NPROC=32:32`, with dropped capabilities and no new privileges, supplies the guest process ceiling; finite exhaustion and attempted hard-limit elevation are checked. Output overflow requires continued bounded-memory discarding during termination to avoid blocking guest IO teardown. Both findings and their corrected real results are documented in the [host guide](../gcp-m0-host.md).

Workload memory quotas do not include all guest-kernel, hypervisor, filesystem-helper, or trusted-controller overhead. M0 runs sequentially on a dedicated host. Host-wide admission control, aggregate resource reservations, bounded persistent storage, and crash-resistant leases remain required before a private pilot.

The Engine API is fixed at version 1.47 and checked against server minimum/maximum versions. Unsupported versions are refused. The image is pinned by digest, the build backend is pinned, and M0 has no model/provider dependency. Future updates must rerun relevant checks; do not infer perpetual compatibility from this record.

## Primary references

- [Docker Engine API v1.47](https://docs.docker.com/reference/api/engine/version/v1.47/) — structured engine operations.
- [Docker runtime options](https://docs.docker.com/reference/cli/docker/container/run/) and [tmpfs mounts](https://docs.docker.com/engine/storage/tmpfs/) — resource and filesystem configuration; these must still be tested.
- [Docker seccomp](https://docs.docker.com/engine/security/seccomp/) — syscall filtering is a distinct layer from capabilities and read-only storage.
- [Kata/containerd integration](https://github.com/kata-containers/kata-containers/blob/main/docs/how-to/containerd-kata.md) and [installation prerequisites](https://github.com/kata-containers/kata-containers/blob/main/docs/install/README.md) — the proposed reference runtime and virtualization requirements.
- [QEMU virt machine](https://www.qemu.org/docs/master/system/arm/virt) and [security model](https://www.qemu.org/docs/master/system/security.html) — accelerator and guest-isolation assumptions. Current upstream documentation is not proof of capability in the locally installed QEMU version.

Documentation was consulted on 2026-09-17. None of these sources certifies Prism's implementation.
