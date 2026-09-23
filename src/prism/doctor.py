"""Read-only checks. Prerequisites never imply validated isolation."""

import os
from pathlib import Path
import platform
import shutil
import sys

from prism.engine import Engine, EngineError, IMAGE


def diagnose(engine: Engine, profile: str = "development") -> dict:
    checks = []

    def add(name, status, detail, remedy=""):
        checks.append({"id": name, "status": status, "detail": detail, "remedy": remedy})

    add("python", "pass" if sys.version_info >= (3, 13) else "blocked", platform.python_version(), "Use Python 3.13 or newer.")
    add("platform", "pass" if platform.system() in ("Linux", "Darwin") else "blocked", f"{platform.system()} / {platform.machine()}")
    if profile == "pilot":
        add("linux_host", "pass" if platform.system() == "Linux" else "blocked", "Pilot execution requires an explicitly authorized Linux host.")
        kvm = Path("/dev/kvm")
        add("kvm_access", "observed" if kvm.exists() and os.access(kvm, os.R_OK | os.W_OK) else "blocked", "Device accessibility is only a prerequisite; nested virtualization is not proven.")
        add("kata_binary", "observed" if shutil.which("kata-runtime") else "blocked", "Binary presence does not establish a configured runtime.")
        add("kata_adapter", "unverified", "The pilot adapter and behavioral validation are not implemented in M0 development mode.")
        add("pilot_release", "blocked", "Identity, grants, leases and the full release gates have not been implemented.")
    else:
        try:
            version = engine.version()
            add("engine", "pass", f"Linux engine {version['Version']} / {version['Arch']}; API {version['ApiVersion']}")
            capabilities = engine.info()
            add("cgroups_seccomp", "observed", f"cgroups v{capabilities['cgroup_version']}; seccomp advertised; behavior requires selftest.")
            if capabilities.get("seccomp_default_unconfined"):
                add("daemon_seccomp_default", "warning", "The daemon default is unconfined. Prism explicitly requests builtin seccomp for each synthetic container; selftest must verify it.")
            try:
                engine.image()
                add("fixture_image", "pass", "Pinned synthetic fixture image is present.")
            except EngineError as exc:
                add("fixture_image", "blocked", "Pinned image missing or invalid.", "Run prism runtime prepare after approving the public image download.")
        except EngineError:
            add("engine", "blocked", "Local Linux Docker engine is unavailable or incompatible.", "Start an authorized local Docker engine; check the socket and API/cgroup support.")
        add("isolation", "unverified", "Development containers have not been validated by this read-only check.", "Run prism selftest --profile development.")
    ready = profile == "development" and not any(c["status"] == "blocked" for c in checks)
    return {
        "schema_version": 1,
        "kind": "host_diagnostics",
        "profile": profile,
        "prerequisites_ready": ready,
        "pilot_ready": False,
        "fixture_image": IMAGE,
        "checks": checks,
        "notice": "Prerequisite observations are not isolation guarantees. Development mode permits synthetic fixtures only.",
    }
