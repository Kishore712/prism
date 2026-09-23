"""Narrow local Docker transport. No Docker context, config, or credentials read."""

from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import platform
import socket
import time
from urllib.parse import urlencode, quote


IMAGE = "docker.io/library/python@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a"
API = "/v1.47"


class EngineError(RuntimeError):
    """Deliberately safe diagnostics; do not reflect engine payloads or paths."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def default_socket() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / ".docker/run/docker.sock"
    return Path("/var/run/docker.sock")


def socket_path(value: str | Path | None) -> Path:
    path = default_socket() if value is None else Path(value)
    if not path.is_absolute() or "\x00" in str(path) or "://" in str(value):
        raise ValueError("Use an absolute local Unix socket path, not a remote endpoint.")
    return path


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(str(self.path))
        except BaseException:
            self.sock.close()
            raise


class Engine:
    def __init__(self, path: str | Path | None = None, timeout: float = 3):
        self.path = socket_path(path)
        self.timeout = timeout

    @contextmanager
    def response(self, method: str, endpoint: str, body=None, *, timeout=None):
        conn = UnixConnection(self.path, timeout or self.timeout)
        try:
            payload = None if body is None else json.dumps(body).encode()
            headers = {} if payload is None else {"Content-Type": "application/json"}
            conn.request(method, endpoint, body=payload, headers=headers)
            response = conn.getresponse()
            if not 200 <= response.status < 300:
                raise EngineError(f"Local engine request failed (HTTP {response.status}).", response.status)
            yield response
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise EngineError("Local engine unavailable or returned an invalid response.") from exc
        finally:
            conn.close()

    def request(self, method: str, endpoint: str, body=None):
        with self.response(method, endpoint, body) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise EngineError("Local engine response exceeded the diagnostic limit.")
            if not data:
                return None
            try:
                return json.loads(data)
            except (ValueError, UnicodeError) as exc:
                raise EngineError("Local engine returned invalid JSON.") from exc

    def version(self):
        data = self.request("GET", "/version")
        if not isinstance(data, dict):
            raise EngineError("Local engine did not return version information.")
        try:
            parse = lambda s: tuple(int(x) for x in s.split("."))
            supported = parse(data["MinAPIVersion"]) <= (1, 47) <= parse(data["ApiVersion"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise EngineError("Local engine version information is incomplete.") from exc
        if not supported or data.get("Os") != "linux" or not data.get("Version") or not data.get("Arch"):
            raise EngineError("A Linux engine supporting API 1.47 is required.")
        return {k: data[k] for k in ("Version", "ApiVersion", "Os", "Arch")}

    def info(self):
        data = self.request("GET", API + "/info")
        if not isinstance(data, dict) or data.get("CgroupVersion") != "2":
            raise EngineError("A Linux engine with cgroups v2 is required for these probes.")
        security = data.get("SecurityOptions", [])
        if not isinstance(security, list) or not any(isinstance(item, str) and "seccomp" in item for item in security):
            raise EngineError("The engine must advertise seccomp support.")
        return {
            "cgroup_version": data["CgroupVersion"],
            "memory_bytes": data.get("MemTotal", 0),
            "cpu_count": data.get("NCPU", 0),
            "seccomp_available": True,
            "seccomp_default_unconfined": any("profile=unconfined" in item for item in security if isinstance(item, str)),
        }

    def image(self):
        data = self.request("GET", API + "/images/" + quote(IMAGE, safe="") + "/json")
        if not isinstance(data, dict) or not isinstance(data.get("Id"), str):
            raise EngineError("Pinned fixture image has invalid metadata.")
        digests = data.get("RepoDigests") or []
        digest = IMAGE.split("@", 1)[1]
        if not any(d.endswith("@" + digest) for d in digests):
            raise EngineError("Fixture image does not match the pinned repository digest.")
        if data.get("Config", {}).get("Volumes"):
            raise EngineError("Fixture images with implicit volumes are not supported.")
        return data["Id"]

    def prepare(self):
        """The only pull path: one fixed public image, no registry credentials."""
        self.version()
        self.info()
        try:
            return {"image_id": self.image(), "downloaded": False}
        except EngineError as exc:
            if exc.status != 404:
                raise
        deadline = time.monotonic() + 180
        with self.response("POST", API + "/images/create?" + urlencode({"fromImage": IMAGE}), timeout=20) as response:
            total = 0
            while True:
                line = response.readline(65537)
                if not line:
                    break
                total += len(line)
                if len(line) > 65536 or total > 8 * 1024 * 1024 or time.monotonic() > deadline:
                    raise EngineError("Fixture image download exceeded its progress/time limit.")
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise EngineError("Image download returned invalid progress.") from exc
                if event.get("error") or event.get("errorDetail"):
                    raise EngineError("Unable to download the pinned public fixture image.")
        return {"image_id": self.image(), "downloaded": True}
