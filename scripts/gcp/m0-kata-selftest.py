"""Dedicated Linux host spike; fixed synthetic work only, not a pilot executor.

Run with Python 3.12+ on the prepared host, with the reviewed probe.py beside it.
The application CLI retains its separate Python requirement and dev-only profile.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import subprocess
import tempfile
import threading
import time
import uuid

IMAGE = "docker.io/library/python@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a"
PROBE_SHA256 = "8bb6f234a9d9cb3f03d55a5df1fe701e40b943e89508c9d4f08d23aecd4725b9"
LABEL = "org.prism.m0-probe"
CLI = ["/usr/local/bin/nerdctl", "--address", "/run/containerd/containerd.sock", "--namespace", "prism-m0"]
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"}
LIMIT = 65536


def guest_helpers():
    """Read process names only on this dedicated host; never collect arguments."""
    found = []
    for path in Path('/proc').glob('[0-9]*/comm'):
        try:
            name = path.read_text().strip()
        except FileNotFoundError:
            continue
        if name.startswith(('qemu-system', 'virtiofsd', 'containerd-shim')):
            found.append(name)
    return sorted(found)


def command(arguments, *, check=True):
    result = subprocess.run(CLI + arguments, env=ENV, capture_output=True, timeout=20)
    if len(result.stdout) + len(result.stderr) > 1024 * 1024:
        raise RuntimeError("Unexpected management response size")
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[:1500])
    return result


def owned_info(name, token):
    result = command(["inspect", name], check=False)
    if result.returncode:
        remaining = command(["ps", "-aq", "--filter", "name=" + name]).stdout.strip()
        if not remaining:
            return None
        raise RuntimeError("Cannot inspect the named probe resource")
    info = json.loads(result.stdout)[0]
    if info.get("Config", {}).get("Labels", {}).get(LABEL) != token:
        raise RuntimeError("Probe ownership mismatch; refusing mutation")
    return info


def cleanup(name, token):
    if owned_info(name, token) is not None:
        command(["rm", "--force", name])
    if command(["ps", "-aq", "--filter", "name=" + name]).stdout.strip():
        raise RuntimeError("Probe removal was not confirmed")


def discard_until_stopped(selector, stop):
    # An attached client must keep draining while the runtime tears down guest IO.
    # Retain no more bytes after the cap; never wait for guest output to finish.
    while selector.get_map() and not stop.is_set():
        for key, _ in selector.select(timeout=0.05):
            if not os.read(key.fileobj.fileno(), 4096):
                selector.unregister(key.fileobj)


def run_probe(program, action, argument=None, *, interruption=None):
    if action not in {"evaluate", "boundaries", "processes", "memory", "flood", "sleep"}:
        raise ValueError("Unknown fixed probe")
    if action == "evaluate" and (type(argument) is not int or not 0 <= argument <= 1000000):
        raise ValueError("Invalid seed")
    token = uuid.uuid4().hex
    name = "prism-m0-probe-" + token
    arguments = [action] + ([] if argument is None else [str(argument)])
    if action == "boundaries":
        arguments.append("rlimit")
    prefix = ('import json,platform,pathlib; print(json.dumps({"guest_kernel":platform.release(),'
              '"guest_boot_id":pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()}),flush=True)\n')
    argv = CLI + [
        "run", "--pull=never", "--name", name, "--label", LABEL + "=" + token,
        "--runtime", "io.containerd.kata.v2", "--network", "none", "--read-only",
        "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.5", "--pids-limit", "32",
        "--ulimit", "nproc=32:32",
        "--tmpfs", "/scratch:rw,noexec,nosuid,nodev,size=8m,mode=1777", "--workdir", "/scratch",
        "--log-driver", "none", "--env", "HOME=/nonexistent",
        IMAGE, "python3", "-I", "-B", "-u", "-c", prefix + program, *arguments,
    ]
    started = time.monotonic()
    child_seen_at = None
    reason = "exited"
    stdout, stderr = bytearray(), bytearray()
    process = subprocess.Popen(argv, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, stdout)
            selector.register(process.stderr, selectors.EVENT_READ, stderr)
            while selector.get_map():
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = LIMIT - len(stdout) - len(stderr)
                    key.data.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        reason = "output_limit"
                if child_seen_at is None and b'"is_child": true' in stdout:
                    child_seen_at = time.monotonic()
                if reason == "exited" and interruption and child_seen_at is not None:
                    if time.monotonic() - child_seen_at >= 1:
                        reason = interruption
                if reason == "exited" and time.monotonic() - started >= 90:
                    reason = "startup_or_execution_timeout"
                if reason != "exited":
                    stop_drain = threading.Event()
                    drain = threading.Thread(target=discard_until_stopped, args=(selector, stop_drain), daemon=True)
                    drain.start()
                    try:
                        if owned_info(name, token) is not None:
                            command(["kill", "--signal", "KILL", name], check=False)
                        cleanup(name, token)
                    finally:
                        stop_drain.set()
                        drain.join(timeout=1)
                        if drain.is_alive():
                            raise RuntimeError("Output drain did not stop")
                    break
        try:
            exit_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("Probe client did not terminate")
        info = owned_info(name, token)
        if info and info.get("State", {}).get("Running"):
            raise RuntimeError("Task still running after client exit")
    finally:
        try:
            cleanup(name, token)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
    output = stdout.decode(errors="replace")
    metadata = json.loads(output.splitlines()[0]) if output.startswith('{"guest_kernel"') else {}
    return {
        "action": action, "exit_code": exit_code, "stop_reason": reason,
        "elapsed_seconds": round(time.monotonic() - started, 3), "cleaned_up": True,
        "child_observed": child_seen_at is not None, "retained_bytes": len(stdout) + len(stderr),
        **metadata,
    }, output, stderr.decode(errors="replace")


def main():
    if os.geteuid() != 0 or platform.system() != "Linux":
        raise SystemExit("Run only as the trusted operator of the prepared Linux test host")
    preflight = json.loads(Path('/var/lib/prism/m0-host-preflight.json').read_text())
    if not preflight.get('prerequisites_ready'):
        raise SystemExit("Host preflight has not passed")
    if guest_helpers():
        raise SystemExit("Dedicated host still has guest runtime processes; inspect before testing")
    program = Path(__file__).with_name("probe.py").read_text()
    if hashlib.sha256(program.encode()).hexdigest() != PROBE_SHA256:
        raise SystemExit("Unexpected synthetic probe bytes")
    checks, runs = [], []

    def record(name, passed, detail):
        checks.append({"id": name, "status": "pass" if passed else "fail", "detail": detail})
        print("PRISM_M0_CHECK " + name + ": " + ("PASS" if passed else "FAIL"), flush=True)

    def run(action, argument=None, **kwargs):
        result, output, error = run_probe(program, action, argument, **kwargs)
        runs.append(result)
        if result['stop_reason'] == 'exited' and result['exit_code'] != 0 and action != 'memory':
            raise RuntimeError("Probe failed: " + error[:1500] + output[-1500:])
        return result, output

    def data(action, argument=None):
        result, output = run(action, argument)
        if result['exit_code'] != 0 or result['stop_reason'] != 'exited':
            raise RuntimeError('Expected successful fixed probe execution')
        return json.loads(output.splitlines()[-1])

    started = time.monotonic()
    previous = os.environ.get("PRISM_PRIVATE_CANARY")
    os.environ["PRISM_PRIVATE_CANARY"] = "PRISM_SYNTHETIC_ENVIRONMENT_CANARY"
    try:
        with tempfile.TemporaryDirectory(prefix="prism-excluded-source-") as directory:
            canary = Path(directory) / "private-canary.txt"
            canary.write_text("PRISM_SYNTHETIC_PRIVATE_CANARY")
            digest = hashlib.sha256(canary.read_bytes()).hexdigest()
            first = data("evaluate", 7)
            repeat = data("evaluate", 7)
            changed = data("evaluate", 19)
            record("real_evaluation", first['synthetic'] and first['samples'] == 8, "Fixed synthetic evaluation ran inside Kata.")
            record("repeatable_evaluation", first == repeat, "Identical approved seed produced identical output.")
            record("parameter_change", first != changed and changed['seed'] == 19, "Another allowed seed executed.")
            for name, passed in data("boundaries", str(canary)).items():
                record(name, passed is True, "Observed inside the actual Kata workload.")
            second = data("boundaries", str(canary))
            record("separate_scratch", second['session_marker_absent'], "A later VM did not inherit the scratch marker.")
            record("source_unchanged", hashlib.sha256(canary.read_bytes()).hexdigest() == digest, "Host canary was not mounted or changed.")
            record("process_exhaustion", data("processes")['process_creation_blocked'], "Finite process creation reached its limit.")
            memory, _ = run("memory")
            record("allocation_terminated", memory['exit_code'] == 137 and memory['stop_reason'] == 'exited', "Allocation beyond the observed memory limit ended with exit 137; no controller kill was requested. This is not an OOM-event API claim.")
            flood, _ = run("flood")
            record("output_flood", flood['stop_reason'] == 'output_limit' and flood['retained_bytes'] <= LIMIT, "Bounded output collection stopped the workload.")
            for interruption in ("timeout", "cancelled"):
                result, _ = run("sleep", interruption=interruption)
                record(interruption, result['stop_reason'] == interruption and result['child_observed'] and result['cleaned_up'], "An observed parent/child workload was killed and removed.")
            record("distinct_guest_kernels", all(r.get('guest_kernel') and r['guest_kernel'] != platform.release() for r in runs), "Every fixed probe observed a guest kernel different from the host.")
            boot_ids = [r.get('guest_boot_id') for r in runs]
            record("independent_guest_boots", all(boot_ids) and len(set(boot_ids)) == len(runs), "Every run observed a different guest boot ID.")
            record("cleanup", all(r['cleaned_up'] for r in runs), "Every owned container was confirmed absent after removal.")
    except Exception as error:
        record("execution_error", False, str(error)[:3000])
    finally:
        if previous is None:
            os.environ.pop("PRISM_PRIVATE_CANARY", None)
        else:
            os.environ["PRISM_PRIVATE_CANARY"] = previous
    for _ in range(20):
        if not guest_helpers():
            break
        time.sleep(0.25)
    remaining_helpers = guest_helpers()
    record("guest_helpers_removed", not remaining_helpers, "Remaining guest runtime process names: " + json.dumps(remaining_helpers))
    report = {
        "schema_version": 1, "kind": "reference_linux_kata_spike", "recorded_at": datetime.now(timezone.utc).isoformat(),
        "host_kernel": platform.release(), "host_arch": platform.machine(), "controller_python": platform.python_version(),
        "fixture_image": IMAGE, "program_sha256": PROBE_SHA256, "elapsed_seconds": round(time.monotonic() - started, 3),
        "passed": bool(checks) and all(c['status'] == 'pass' for c in checks), "pilot_ready": False,
        "checks": checks, "runs": runs,
        "unverified": ["sandbox escape resistance", "durable host leases and controller crash recovery", "identity and grants", "aggregate budgets", "private data handling", "model integration"],
    }
    Path('/var/lib/prism/m0-kata-selftest.json').write_text(json.dumps(report, indent=2) + '\n')
    print("PRISM_M0_KATA_RESULT " + json.dumps(report), flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == "__main__":
    main()
