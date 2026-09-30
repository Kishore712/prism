"""Trusted one-job controller. Never loads a model key or a source workspace."""

import hashlib
import json
import os
import selectors
import signal
import stat
import sys
import threading
import time

from prism.engine import Engine, EngineError
from prism.reference_runtime import ReferenceLinuxRuntime
from prism.runtime import DevelopmentRuntime, action_program

LEASE_TTL_SECONDS = 3.0  # Controller loss cancels a fixed Kata job within this bound.


def _start_lease_monitor(argument: str, cancelled: threading.Event) -> bool:
    """Require a live, one-byte parent heartbeat before creating Kata resources."""
    if not argument.isascii() or not argument.isdecimal() or len(argument) > 6:
        return False
    fd = int(argument)
    if fd < 3:
        return False
    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            return False
        # The descriptor is only for this controller; Kata's child must not
        # retain the read end and mask a parent EOF.
        os.set_inheritable(fd, False)
    except OSError:
        return False
    first_lease = threading.Event()

    def watch():
        last_renewal = time.monotonic()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(fd, selectors.EVENT_READ)
                while not cancelled.is_set():
                    remaining = LEASE_TTL_SECONDS - (time.monotonic() - last_renewal)
                    if remaining <= 0:
                        cancelled.set()
                        break
                    if not selector.select(timeout=remaining):
                        cancelled.set()
                        break
                    value = os.read(fd, 4096)
                    if not value or any(byte != ord("L") for byte in value):
                        cancelled.set()
                        break
                    last_renewal = time.monotonic()
                    first_lease.set()
        except (OSError, ValueError):
            cancelled.set()
        finally:
            os.close(fd)
            first_lease.set()

    threading.Thread(target=watch, daemon=True).start()
    first_lease.wait(LEASE_TTL_SECONDS + 0.1)
    return first_lease.is_set() and not cancelled.is_set()


def main():
    cancelled = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    if len(sys.argv) not in (4, 5, 8) or sys.argv[2] not in (
        "bootstrap",
        "json-check",
        "python-workspace",
    ):
        return 2
    action = sys.argv[2]
    profile = "development" if len(sys.argv) in (4, 5) else sys.argv[4]
    if profile not in ("development", "reference-linux"):
        return 2
    if profile == "reference-linux" and (action != "json-check" or len(sys.argv) != 8):
        return 2
    if profile == "development" and len(sys.argv) != (
        5 if action == "python-workspace" else 4
    ):
        return 2
    try:
        expected_hash = hashlib.sha256(action_program(action).encode()).hexdigest()
    except ValueError:
        return 2
    if expected_hash != sys.argv[3]:
        return 2
    if profile == "reference-linux" and not _start_lease_monitor(
        sys.argv[7], cancelled
    ):
        return 2
    if action == "python-workspace" and not _start_lease_monitor(
        sys.argv[4], cancelled
    ):
        return 2
    try:
        limit = 160 * 1024 if action == "python-workspace" else 100 * 1024
        raw_argument = sys.stdin.buffer.read(limit + 1)
        if len(raw_argument) > limit:
            return 2
        decoded = raw_argument.decode("ascii")
        argument = int(decoded) if action == "bootstrap" else decoded
        if (action == "bootstrap" and not 0 <= argument <= 1000) or cancelled.is_set():
            return 2
        runtime = (
            ReferenceLinuxRuntime()
            if profile == "reference-linux"
            else DevelopmentRuntime(Engine(sys.argv[1]))
        )
    except (EngineError, ValueError):
        return 2
    try:
        result = (
            runtime.run_python(argument, timeout=30, cancel=cancelled)
            if action == "python-workspace"
            else runtime.run(
                "evaluate" if action == "bootstrap" else "json-check",
                argument,
                timeout=10,
                cancel=cancelled,
                **(
                    {"name": sys.argv[5], "token": sys.argv[6]}
                    if profile == "reference-linux"
                    else {}
                ),
            )
        )
        if profile == "reference-linux" and cancelled.is_set():
            result.stop_reason = "cancelled"
        print(json.dumps(result.as_dict()), flush=True)
        return 0
    except (EngineError, ValueError):
        # Conservatively require reconciliation after an execution exception.
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
