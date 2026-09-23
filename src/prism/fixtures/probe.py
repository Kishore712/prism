"""Fixed synthetic probes, executed only inside the explicitly selected runtime."""

import errno
import json
import os
from pathlib import Path
import random
import resource
import signal
import socket
import sys
import time


def evaluate(seed):
    if type(seed) is not int or not 0 <= seed <= 1_000_000:
        raise ValueError("Seed must be an integer between 0 and 1000000.")
    differences = [0.03, -0.01, 0.07, 0.02, 0.00, 0.05, -0.02, 0.04]
    rng = random.Random(seed)
    bootstrap = sorted(sum(rng.choices(differences, k=len(differences))) / len(differences) for _ in range(300))
    return {"seed": seed, "samples": len(differences), "mean_difference": sum(differences) / len(differences), "bootstrap_interval": [bootstrap[7], bootstrap[292]], "resamples": 300, "synthetic": True}


def boundaries(host_canary, process_control="cgroup"):
    checks = {}
    checks["non_root"] = os.getuid() == 65534
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    checks["no_new_privileges"] = status["NoNewPrivs"].strip() == "1"
    checks["capabilities_dropped"] = int(status["CapEff"].strip(), 16) == 0
    checks["seccomp_active"] = status["Seccomp"].strip() == "2"
    checks["host_canary_absent"] = not Path(host_canary).exists()
    checks["credentials_absent"] = "PRISM_PRIVATE_CANARY" not in os.environ
    checks["control_sockets_absent"] = all(not Path(p).exists() for p in ["/var/run/docker.sock", "/run/containerd/containerd.sock"])
    checks["session_marker_absent"] = not Path("/scratch/session-marker").exists()
    try:
        Path("/prism-forbidden-write").write_text("denied")
        checks["root_read_only"] = False
    except OSError as exc:
        checks["root_read_only"] = (
            exc.errno in (errno.EROFS, errno.EACCES)
            and bool(os.statvfs("/").f_flag & os.ST_RDONLY)
        )
    # Some kernels create down tunnel devices even in a fresh network namespace.
    # Check usable interfaces, not their mere presence or sysfs control files.
    interfaces = [name for _, name in socket.if_nameindex()]
    checks["no_active_external_interface"] = all(
        name == "lo" or not (int(Path(f"/sys/class/net/{name}/flags").read_text(), 16) & 1)
        for name in interfaces
    )
    for name, address in [("external_network_denied", "192.0.2.1"), ("metadata_network_denied", "169.254.169.254")]:
        with socket.socket() as client:
            client.settimeout(0.4)
            try:
                client.connect((address, 80))
                checks[name] = False
            except OSError as exc:
                checks[name] = exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH)
    Path("/scratch/session-marker").write_text("synthetic session output")
    checks["scratch_writable"] = Path("/scratch/session-marker").read_text() == "synthetic session output"
    checks["memory_limit"] = int(Path("/sys/fs/cgroup/memory.max").read_text()) == 128 * 1024 * 1024
    checks["swap_disabled"] = Path("/sys/fs/cgroup/memory.swap.max").read_text().strip() == "0"
    if process_control == "cgroup":
        checks["process_limit"] = Path("/sys/fs/cgroup/pids.max").read_text().strip() == "32"
    elif process_control == "rlimit":
        checks["process_limit"] = resource.getrlimit(resource.RLIMIT_NPROC) == (32, 32)
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (33, 33))
            checks["process_limit_cannot_be_raised"] = False
        except (ValueError, OSError):
            checks["process_limit_cannot_be_raised"] = resource.getrlimit(resource.RLIMIT_NPROC) == (32, 32)
    else:
        raise ValueError("Unknown process enforcement mode")
    quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
    checks["cpu_limit"] = quota != "max" and int(quota) / int(period) <= 0.5
    target = Path("/scratch/fill")
    try:
        with target.open("wb") as f:
            for _ in range(12):
                f.write(b"x" * 1024 * 1024)
        checks["scratch_limit"] = False
    except OSError as exc:
        checks["scratch_limit"] = exc.errno in (errno.ENOSPC, errno.EFBIG)
    finally:
        target.unlink(missing_ok=True)
    return checks


def process_limit():
    children = []
    blocked = False
    try:
        for _ in range(40):
            try:
                child = os.fork()
            except OSError as exc:
                blocked = exc.errno == errno.EAGAIN
                break
            if child == 0:
                time.sleep(60)
                os._exit(0)
            children.append(child)
        return {"process_creation_blocked": blocked, "children_created": len(children)}
    finally:
        for child in children:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(child, 0)


def main():
    action = sys.argv[1]
    if action == "evaluate":
        result = evaluate(int(sys.argv[2]))
    elif action == "boundaries":
        result = boundaries(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else "cgroup")
    elif action == "processes":
        result = process_limit()
    elif action == "memory":
        blocks = []
        for _ in range(256):
            blocks.append(bytearray(1024 * 1024))
        result = {"unexpected_allocation_success": True}
    elif action == "flood":
        for _ in range(4096):
            os.write(1, b"x" * 4096)
        time.sleep(30)
        result = {"unexpected_completion": True}
    elif action == "sleep":
        child = os.fork()
        print(json.dumps({"child_started": True, "is_child": child == 0}), flush=True)
        time.sleep(60)
        result = {"unexpected_completion": True}
    else:
        raise ValueError("Unknown built-in probe.")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
