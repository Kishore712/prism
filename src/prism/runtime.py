"""Fixed synthetic probes, not an arbitrary task executor or pilot sandbox."""

import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from importlib.resources import files

from prism.engine import API, Engine, EngineError

OUTPUT_LIMIT = 64 * 1024
LABEL = "org.prism.synthetic-selftest"
PROBES = {
    "evaluate",
    "boundaries",
    "processes",
    "memory",
    "flood",
    "sleep",
    "json-check",
}


def probe_program() -> str:
    return files("prism.fixtures").joinpath("probe.py").read_text()


def json_check_program() -> str:
    return files("prism.fixtures").joinpath("json_check.py").read_text()


def action_program(action: str) -> str:
    if action == "bootstrap":
        return probe_program()
    if action == "json-check":
        return json_check_program()
    raise ValueError("Only built-in actions are accepted.")


def probe_arguments(action: str, argument=None) -> list[str]:
    if action not in PROBES:
        raise ValueError("Only the built-in synthetic probes are accepted.")
    if action == "evaluate":
        if type(argument) is not int or not 0 <= argument <= 1_000_000:
            raise ValueError("Seed must be an integer between 0 and 1000000.")
        return [action, str(argument)]
    if action == "boundaries":
        if (
            not isinstance(argument, str)
            or not argument.startswith("/")
            or len(argument) > 1024
            or "\x00" in argument
        ):
            raise ValueError("A bounded absolute synthetic canary path is required.")
        return [action, argument]
    if action == "json-check":
        if (
            not isinstance(argument, str)
            or not 1 <= len(argument) <= 100 * 1024
            or not argument.isascii()
        ):
            raise ValueError("JSON check payload exceeds its fixed bound.")
        return [argument]
    if argument is not None:
        raise ValueError("This probe does not accept parameters.")
    return [action]


def container_spec(image_id: str, action: str, argument, token: str) -> dict:
    args = probe_arguments(action, argument)
    program = json_check_program() if action == "json-check" else probe_program()
    return {
        "Image": image_id,
        "Entrypoint": ["python3"],
        "Cmd": ["-I", "-B", "-u", "-c", program, *args],
        "User": "65534:65534",
        "WorkingDir": "/scratch",
        "Env": [
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "LANG=C.UTF-8",
            "HOME=/nonexistent",
        ],
        "NetworkDisabled": True,
        "OpenStdin": False,
        "Tty": False,
        "Healthcheck": {"Test": ["NONE"]},
        "Labels": {LABEL: token},
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true", "seccomp=builtin"],
            "Memory": 128 * 1024 * 1024,
            "MemorySwap": 128 * 1024 * 1024,
            "NanoCpus": 500_000_000,
            "PidsLimit": 32,
            "Tmpfs": {"/scratch": "rw,noexec,nosuid,nodev,size=8m,mode=1777"},
            "IpcMode": "none",
            "CgroupnsMode": "private",
            "Init": True,
            "Privileged": False,
            "PublishAllPorts": False,
            "RestartPolicy": {"Name": "no"},
            "LogConfig": {
                "Type": "local",
                "Config": {"max-size": "1m", "max-file": "1", "compress": "false"},
            },
            "Ulimits": [
                {"Name": "nofile", "Soft": 64, "Hard": 64},
                {"Name": "core", "Soft": 0, "Hard": 0},
            ],
        },
    }


@dataclass
class RunResult:
    action: str
    exit_code: int
    stop_reason: str
    oom_killed: bool
    stdout: str
    stderr: str
    output_limited: bool
    cleaned_up: bool
    elapsed_seconds: float
    image_id: str
    program_sha256: str
    runtime_handler: str | None = None
    guest_kernel: str | None = None
    guest_boot_id: str | None = None

    def as_dict(self):
        return asdict(self)

    def json_output(self):
        if self.exit_code != 0 or self.stop_reason != "exited" or self.output_limited:
            raise EngineError("Probe did not produce a successful bounded result.")
        try:
            return json.loads(self.stdout)
        except ValueError as exc:
            raise EngineError("Probe returned invalid result JSON.") from exc


class LogReader(threading.Thread):
    def __init__(self, engine: Engine, name: str, timeout: float):
        super().__init__(daemon=True)
        self.engine, self.name, self.timeout = engine, name, timeout
        self.stdout, self.stderr = bytearray(), bytearray()
        self.exceeded = threading.Event()
        self.failed = False

    def run(self):
        try:
            endpoint = API + f"/containers/{self.name}/logs?stdout=1&stderr=1&follow=1"
            with self.engine.response(
                "GET", endpoint, timeout=self.timeout
            ) as response:
                while True:
                    header = response.read(8)
                    if not header:
                        return
                    if (
                        len(header) != 8
                        or header[0] not in (1, 2)
                        or header[1:4] != b"\0\0\0"
                    ):
                        raise EngineError("Invalid log stream frame.")
                    length = int.from_bytes(header[4:], "big")
                    remaining = OUTPUT_LIMIT - len(self.stdout) - len(self.stderr)
                    if length > remaining:
                        self.exceeded.set()
                        return
                    chunk = response.read(length)
                    if len(chunk) != length:
                        raise EngineError("Incomplete log stream frame.")
                    (self.stdout if header[0] == 1 else self.stderr).extend(chunk)
        except EngineError:
            self.failed = True


class DevelopmentRuntime:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.owned = {}
        engine.version()
        engine.info()
        self.image_id = engine.image()

    def _cleanup(self, name: str) -> bool:
        if name not in self.owned:
            raise ValueError("Cannot clean up a resource not created by this runtime.")
        endpoint = API + f"/containers/{name}"
        try:
            info = self.engine.request("GET", endpoint + "/json")
        except EngineError as exc:
            if exc.status == 404:
                self.owned.pop(name)
                return True
            raise
        if info.get("Config", {}).get("Labels", {}).get(LABEL) != self.owned[name]:
            raise EngineError("Resource ownership check failed; cleanup was refused.")
        self.engine.request("DELETE", endpoint + "?force=1&v=1")
        try:
            self.engine.request("GET", endpoint + "/json")
        except EngineError as exc:
            if exc.status == 404:
                self.owned.pop(name)
                return True
            raise
        raise EngineError("Selftest resource removal was not confirmed.")

    def run(
        self, action: str, argument=None, *, timeout: float = 10, cancel=None
    ) -> RunResult:
        if not 0.1 <= timeout <= 30:
            raise ValueError(
                "Synthetic probe timeout must be between 0.1 and 30 seconds."
            )
        token = uuid.uuid4().hex
        name = "prism-m0-" + token
        spec = container_spec(self.image_id, action, argument, token)
        started = time.monotonic()
        reader = None
        result = None
        self.owned[name] = token
        try:
            self.engine.request("POST", API + "/containers/create?name=" + name, spec)
            self.engine.request("POST", API + f"/containers/{name}/start")
            reader = LogReader(self.engine, name, timeout + 6)
            reader.start()
            reason = "exited"
            while True:
                info = self.engine.request("GET", API + f"/containers/{name}/json")
                state = info["State"]
                if not state["Running"]:
                    break
                if cancel is not None and cancel.is_set():
                    reason = "cancelled"
                elif reader.exceeded.is_set():
                    reason = "output_limit"
                elif time.monotonic() - started >= timeout:
                    reason = "timeout"
                if reason != "exited":
                    try:
                        self.engine.request(
                            "POST", API + f"/containers/{name}/kill?signal=SIGKILL"
                        )
                    except EngineError as exc:
                        if exc.status != 409:
                            raise
                    # Observe termination; a successful kill request alone is insufficient.
                    deadline = time.monotonic() + 3
                    while True:
                        state = self.engine.request(
                            "GET", API + f"/containers/{name}/json"
                        )["State"]
                        if not state["Running"]:
                            break
                        if time.monotonic() > deadline:
                            raise EngineError("Job termination could not be confirmed.")
                        time.sleep(0.05)
                    break
                time.sleep(0.05)
            reader.join(timeout=2)
            if reader.is_alive() or reader.failed:
                raise EngineError("Complete bounded probe logs could not be collected.")
            if reader.exceeded.is_set() and reason == "exited":
                reason = "output_limit"
            result = RunResult(
                action,
                state["ExitCode"],
                reason,
                state.get("OOMKilled", False),
                reader.stdout.decode("utf-8", errors="replace"),
                reader.stderr.decode("utf-8", errors="replace"),
                reader.exceeded.is_set(),
                False,
                round(time.monotonic() - started, 3),
                self.image_id,
                hashlib.sha256(
                    (
                        json_check_program()
                        if action == "json-check"
                        else probe_program()
                    ).encode()
                ).hexdigest(),
            )
        finally:
            try:
                cleaned = self._cleanup(name)
            except EngineError as exc:
                raise EngineError(
                    f"Cleanup could not be confirmed for synthetic container {name}. Remove only this named container after inspecting it."
                ) from exc
            if reader is not None:
                reader.join(timeout=1)
            if result is not None:
                result.cleaned_up = cleaned
        return result
