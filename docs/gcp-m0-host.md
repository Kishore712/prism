# GCP Host Preparation for M0

**Status, 2026-09-17:** The dedicated host passed its KVM preflight and 33 real Kata boundary checks across 10 independent guest boots. M0 reference-host feasibility is validated for this configuration. The VM is now stopped (`TERMINATED`), and startup-test metadata has been removed. See the [Linux result](validation/m0-linux-kata.json) and [cloud lifecycle record](validation/m0-cloud-host.json). Collaborator access and private-pilot readiness remain disabled.

The existing local QEMU/HVF guest did not expose KVM. The owner subsequently authorized a Linux test instance using available Google Cloud trial credit. Google Cloud MFA and Compute Engine API activation have been completed by the owner; Cloud Shell account authorization has also been explicitly approved and completed. Do not put account identifiers, credentials, billing identifiers, or personal browser data in repository records.

## Selected configuration

| Item | Created configuration |
| --- | --- |
| Purpose | Dedicated synthetic M0 host validation |
| Instance | `prism-m0-kvm` |
| Region / zone | `us-central1` / `us-central1-a` |
| Machine | Intel `n2-standard-2`, 2 vCPUs, 8 GiB memory |
| OS | Official Ubuntu 24.04 LTS amd64 image |
| Boot disk | 30 GiB balanced persistent disk |
| Virtualization | Explicit nested virtualization; verify the actual KVM API afterward |
| Runtime limit | Four hours followed by STOP, no automatic restart after host failure |
| Cloud identity | No attached service account or API scopes |
| Network | Dedicated custom VPC/subnet; no ingress firewall allow rule, no application ports |
| Host downloads | Ephemeral external IPv4 for outbound package retrieval; no reserved static address |
| Guest access | Block inherited project SSH keys; no new SSH key or password created |
| First boot | Reviewed [host preflight](../scripts/gcp/m0-preflight.sh) only |

The reference on-demand N2 compute rate consulted was USD 0.097118 per hour in Iowa, before disk and network charges. A four-hour run is approximately USD 0.39 for compute. The console may show a lower monthly average that assumes sustained use; do not use that average to promise the cost of a short run. Stopping compute does not delete its disk or stop disk storage charges. A runtime limit is not a total billing cap, and it resets when the instance is started again. Keep the account in its existing trial state; an account upgrade or new financial commitment is not authorized here.

## Host preflight

The reviewed [creation script](../scripts/gcp/create-m0-host.sh) takes an explicit project ID, resolves the official image, and creates only the named test VPC, subnet, and VM. It stops on errors and never adopts, modifies, or deletes an existing resource. A partial failure can leave the newly created VPC or subnet; inspect those exact names before any retry. Run it only from an authorized GCP environment, with the preflight script beside it. It does not activate billing, enable APIs, create IAM grants, or install gcloud.

The startup script writes a minimal JSON record to `/var/lib/prism/m0-host-preflight.json` and emits the same record with the marker `PRISM_M0_PREFLIGHT` in startup output. Read that output through the authenticated cloud control plane. Interactive serial-console access is not needed.

It checks x86_64, cgroups v2, `/dev/kvm`, KVM API version 12, and creation and immediate closure of an empty KVM VM handle. It reads no project files, credentials, host environment dump, or metadata tokens. It installs no software and changes no accounts or networking. Syntax checking alone does not establish that it ran successfully on Linux.

A successful empty VM handle is only a prerequisite. The subsequent runtime validation booted actual guests and tested normal execution, blocked access, resource limits, termination, and cleanup. Even with that evidence, `pilot_ready` stays false until the broader product gates pass.

## Runtime installation and validation

The dedicated host ran the reviewed [Kata bootstrap](../scripts/gcp/m0-kata-bootstrap.sh). It downloads the official Kata 4.2.0 and nerdctl-full 2.3.5 archives and verifies their pinned SHA-256 values before extraction. The latter includes containerd 2.3.3. This is privileged host setup, not an operation available to a collaborator. Never run it on an unrelated or shared host. It refuses an existing Kata/containerd installation rather than overwriting it.

The actual preflight observed x86_64, Linux `7.0.0-1011-gcp`, two CPUs, cgroups v2, KVM API 12, and successful creation/closure of an empty KVM VM handle. `kata-runtime check` subsequently reported that the system can create Kata containers. The first bootstrap found a startup race: systemd's `Type=simple` service returned before containerd's socket was ready. The script now waits for a bounded successful API response before pulling the image. A capability message or installed binary does not establish successful workload isolation.

The [Linux spike](../scripts/gcp/m0-kata-selftest.py) uses the same reviewed synthetic fixture as the development CLI, explicitly selects `io.containerd.kata.v2`, and never falls back to runc. It requires Python 3.12+ on the prepared Linux host; the application CLI's Python 3.13 requirement is separate. Fixed probes cover guest identity, filesystem/network denial, resource limits, output bounding, and scoped cancellation/removal. Each run records the guest kernel and boot ID to distinguish real guest execution from host execution or reuse of one guest.

The first actual guest test exposed a process-limit mismatch: Kata's Go runtime removes `Linux.Resources.Pids` before sending the OCI specification into the guest, and the observed guest `pids.max` was `max`. The revised launcher additionally sets OCI `RLIMIT_NPROC` soft/hard limits to 32. This is kernel enforcement for the fixed non-root UID in each separate VM; it is not a claim that guest PID cgroups are configured. The probe must observe both limits, fail to raise them, and hit the limit during finite process creation. This choice relies on the enforced non-root identity, dropped capabilities, and inability to switch UID. Do not extend it to privileged or multi-user guests without redesign and validation. See the [versioned Kata implementation](https://github.com/kata-containers/kata-containers/blob/4.2.0/src/runtime/virtcontainers/kata_agent.go#L1097).

Cleanup checks also require that no QEMU, virtiofsd, or containerd-shim process remains on the dedicated idle test host. Root-filesystem checks require both a denied write and the kernel's read-only mount flag; a non-root permission denial alone is insufficient. Host process inspection collects names only, not command arguments or environment values.

An output-flood run exposed a teardown timeout while the attached client was no longer draining its pipes. The revised controller retains at most 64 KiB, then discards subsequent pipe data concurrently with bounded kill/removal operations. It does not count an attempted removal as successful cleanup. Dedicated-host recovery accepts only an explicit 32-hex probe token (`--recover-probe TOKEN` when preparing the script), verifies its ownership label, and confirms removal. The operator must first inspect the failed run and stop its execution; recovery never prunes a namespace or adopts an unrelated container.

To prepare a startup script locally for an **already installed, dedicated M0 host**:

```bash
python3 scripts/gcp/prepare-kata-validation.py /tmp/prism-m0-kata-validation.sh
bash -n /tmp/prism-m0-kata-validation.sh
```

The output is created exclusively and contains exactly two reviewed source files: the host spike and synthetic fixture. Transfer only that generated script through the authorized Cloud Shell upload UI, verify its checksum, then attach it as startup metadata to the named test VM. A reset reruns startup metadata and interrupts that VM's workloads; use it only while the dedicated test host is idle. It emits `PRISM_M0_CHECK` progress and `PRISM_M0_KATA_RESULT` JSON, also saved at `/var/lib/prism/m0-kata-selftest.json`. Remove test startup metadata when finished so a later boot does not silently rerun resource-exhaustion tests.

These tests do not implement a production executor, trusted durable lease, real grants/revocation, aggregate budgets, or model integration. Controller-driven termination requires a living controller. Exit 137 during an excessive allocation is recorded as allocation termination, not proof of a supported OOM-event API.

## Recorded result and replay

The final run at `2026-09-17T23:19:13.740266+00:00` passed all 33 checks in 46.081 seconds. All ten workloads observed guest kernel `6.18.35` and distinct guest boot IDs, different from the host kernel. Normal evaluations, boundary probes, finite process exhaustion, excessive memory allocation, bounded-output termination, timeout, cancellation with a live child, container removal, and absence of guest runtime helper processes all passed. The final output-flood run retained exactly 65,536 bytes, terminated with exit 137, and cleaned up in 4.269 seconds including guest startup. These are individual functional observations, not benchmark percentiles.

The stopped-state record at `2026-09-17T23:23:13.174921+00:00` confirms no attached service account, zero firewall rules in the dedicated VPC, nested virtualization enabled, a 14,400-second STOP policy, and no startup script. Secure boot, vTPM, and integrity monitoring are enabled. The source image is `ubuntu-2404-noble-amd64-v20260906`. The 30 GiB disk and dedicated VPC/subnet remain; disk storage still consumes credit while compute is stopped. No account upgrade or public application deployment occurred.

To inspect the saved evidence without starting cloud resources:

```bash
python3 - <<'PY'
import json
from pathlib import Path
result = json.loads(Path('docs/validation/m0-linux-kata.json').read_text())
host = json.loads(Path('docs/validation/m0-cloud-host.json').read_text())
assert result['passed'] and not result['pilot_ready']
assert len(result['checks']) == 33 and all(c['status'] == 'pass' for c in result['checks'])
assert len({r['guest_boot_id'] for r in result['runs']}) == 10
assert host['status'] == 'TERMINATED' and not host['startup_script_present']
print('Saved M0 evidence is consistent; this does not rerun the tests.')
PY
```

For a new authorized replay, prepare and upload a fresh script as above, set `PRISM_PROJECT` to the actual authorized project ID, and run these commands in that project's Cloud Shell. Do not run the creation/bootstrap scripts again on the existing installed host.

```bash
gcloud compute instances add-metadata prism-m0-kvm --project="$PRISM_PROJECT" --zone=us-central1-a --metadata-from-file=startup-script=prism-m0-kata-validation.sh
gcloud compute instances start prism-m0-kvm --project="$PRISM_PROJECT" --zone=us-central1-a
gcloud compute instances get-serial-port-output prism-m0-kvm --project="$PRISM_PROJECT" --zone=us-central1-a
```

Allow startup and testing to finish, then require a new `PRISM_M0_KATA_RESULT` timestamp and current program digest. Serial output can include interleaved or repeated messages; parse a complete JSON record and do not mistake an older passing report for a new result. Preserve a failed report and investigate the exact named resource before recovery. Starting the VM consumes compute credit again and resets its four-hour runtime limit. Always finish by removing startup metadata, stopping the VM, and verifying `TERMINATED`:

```bash
gcloud compute instances remove-metadata prism-m0-kvm --project="$PRISM_PROJECT" --zone=us-central1-a --keys=startup-script
gcloud compute instances stop prism-m0-kvm --project="$PRISM_PROJECT" --zone=us-central1-a
gcloud compute instances describe prism-m0-kvm --project="$PRISM_PROJECT" --zone=us-central1-a --format='value(status)'
```

## Acceptance and retention

1. Inspect the created instance's actual machine type, image, nested virtualization setting, runtime limit, attached identities, and network/firewall configuration.
2. Inspect the preflight record; require `prerequisites_ready: true` and `pilot_ready: false`.
3. Do not enable public ingress or attach cloud credentials to make an isolation probe succeed.
4. Stop the instance when active testing is complete and verify its stopped state. Preserve the disk for subsequent authorized M0 work, while reporting that storage remains billable against credit.
5. Record actual runtime validation separately from host creation. Never describe this preparation as a completed security evaluation.

## Primary references

- [Nested virtualization restrictions](https://docs.cloud.google.com/compute/docs/instances/nested-virtualization/overview) and [explicit enablement](https://docs.cloud.google.com/compute/docs/instances/nested-virtualization/enabling).
- [VM runtime limits](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime).
- [General-purpose instance pricing](https://cloud.google.com/products/compute/pricing/general-purpose).
- [Free-trial scope](https://docs.cloud.google.com/free/docs/free-cloud-features).
- [Linux startup scripts](https://docs.cloud.google.com/compute/docs/instances/startup-scripts/linux).
- [Kata 4.2.0 release](https://github.com/kata-containers/kata-containers/releases/tag/4.2.0), [versioned limitations](https://github.com/kata-containers/kata-containers/blob/4.2.0/docs/Limitations.md), and [QEMU configuration](https://github.com/kata-containers/kata-containers/blob/4.2.0/src/runtime/config/configuration-qemu.toml.in).
- [nerdctl 2.3.5 release](https://github.com/containerd/nerdctl/releases/tag/v2.3.5) and [versioned command reference](https://github.com/containerd/nerdctl/blob/v2.3.5/docs/command-reference.md).

Consulted on 2026-09-17. Actual provisioning and runtime results take precedence over expectations from documentation.
