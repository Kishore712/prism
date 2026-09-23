"""Trusted one-job controller. Never loads a model key or a source workspace."""

import hashlib
import json
import signal
import sys
import threading

from prism.engine import Engine, EngineError
from prism.reference_runtime import ReferenceLinuxRuntime
from prism.runtime import DevelopmentRuntime, action_program


def main():
    cancelled = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancelled.set())
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    if len(sys.argv) not in (4, 7) or sys.argv[2] not in ("bootstrap", "json-check"):
        return 2
    action = sys.argv[2]
    profile = "development" if len(sys.argv) == 4 else sys.argv[4]
    if profile not in ("development", "reference-linux"):
        return 2
    if profile == "reference-linux" and (action != "json-check" or len(sys.argv) != 7):
        return 2
    if profile == "development" and len(sys.argv) != 4:
        return 2
    try:
        expected_hash = hashlib.sha256(action_program(action).encode()).hexdigest()
    except ValueError:
        return 2
    if expected_hash != sys.argv[3]:
        return 2
    try:
        raw_argument = sys.stdin.buffer.read(100 * 1024 + 1)
        if len(raw_argument) > 100 * 1024:
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
        result = runtime.run(
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
        print(json.dumps(result.as_dict()), flush=True)
        return 0
    except (EngineError, ValueError):
        # Conservatively require reconciliation after an execution exception.
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
