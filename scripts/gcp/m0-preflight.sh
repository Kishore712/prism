#!/bin/bash
# Trusted startup probe for a new, dedicated GCP test host. No project data.
set -euo pipefail
umask 077
install -d -m 0700 /var/lib/prism
/usr/bin/python3 - <<'PY'
import datetime
import fcntl
import json
import os
import pathlib
import platform

report = {
    "schema_version": 1,
    "purpose": "m0-host-preflight",
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "architecture": platform.machine(),
    "kernel": platform.release(),
    "cpu_count": os.cpu_count(),
    "cgroup_v2": pathlib.Path("/sys/fs/cgroup/cgroup.controllers").is_file(),
    "kvm_device": pathlib.Path("/dev/kvm").exists(),
    "kvm_api_version": None,
    "empty_kvm_vm_created": False,
    "pilot_ready": False,
    "limitation": "Prerequisites only; no guest boot or isolation test has passed.",
}
try:
    fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
    try:
        report["kvm_api_version"] = fcntl.ioctl(fd, 0xAE00, 0)
        vm_fd = fcntl.ioctl(fd, 0xAE01, 0)
        os.close(vm_fd)
        report["empty_kvm_vm_created"] = True
    finally:
        os.close(fd)
except OSError as error:
    report["kvm_error_errno"] = error.errno
report["prerequisites_ready"] = bool(
    report["architecture"] == "x86_64"
    and report["cgroup_v2"]
    and report["kvm_api_version"] == 12
    and report["empty_kvm_vm_created"]
)
payload = json.dumps(report, sort_keys=True)
pathlib.Path("/var/lib/prism/m0-host-preflight.json").write_text(payload + "\n")
print("PRISM_M0_PREFLIGHT " + payload, flush=True)
raise SystemExit(0 if report["prerequisites_ready"] else 1)
PY
