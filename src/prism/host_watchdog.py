"""Root-only, independent reference runtime supervisor.

The identity service leases one exact DB-backed run to this process. A lost
lease stops the entire service cgroup before the named Kata resource is touched.
"""

import argparse
import json
import os
import re
import socket
import sqlite3
import stat
import struct
import subprocess
import time
from contextlib import closing
from pathlib import Path

from prism.engine import EngineError
from prism.reference_runtime import ReferenceLinuxRuntime

SOCKET = Path("/run/prism-host-watchdog/watchdog.sock")
SERVICE = "prism-identity-service.service"
LIFECYCLE = "/usr/local/libexec/prism-identity-service-lifecycle.py"
RUN = re.compile(r"[0-9a-f]{32}\Z")
MAX_MESSAGE = 1024
LEASE_SECONDS = 3.0
ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


class WatchdogError(RuntimeError):
    pass


def _strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise WatchdogError("Duplicate request field")
            result[key] = value
        return result

    def constant(_):
        raise WatchdogError("Invalid JSON constant")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def _start_time(pid):
    """Linux process start tick, immune to PID reuse."""
    try:
        value = Path(f"/proc/{pid}/stat").read_text()
        fields = value.rsplit(") ", 1)[1].split()
        if fields[0] in ("Z", "X"):
            raise WatchdogError("Process has exited")
        return int(fields[19])
    except (OSError, ValueError, IndexError) as exc:
        raise WatchdogError("Process identity is unavailable") from exc


def _cgroup(pid):
    try:
        rows = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    except OSError as exc:
        raise WatchdogError("Process cgroup is unavailable") from exc
    values = [row.split(":", 2)[2] for row in rows if row.startswith("0::")]
    if len(values) != 1:
        raise WatchdogError("Expected one cgroup v2 identity")
    return values[0]


def _service_cgroup(*, allow_empty=False):
    try:
        completed = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "--property=ControlGroup",
                "--value",
                SERVICE,
            ],
            env=ENV,
            capture_output=True,
            timeout=3,
            check=True,
        )
        value = completed.stdout.decode("ascii").strip()
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise WatchdogError("Service cgroup lookup failed") from exc
    if allow_empty and not value:
        return None
    if value != "/system.slice/" + SERVICE:
        raise WatchdogError("Unexpected service cgroup")
    own = _cgroup(os.getpid())
    if own == value or own.startswith(value + "/") or value.startswith(own + "/"):
        raise WatchdogError("Watchdog is not isolated from service cgroup")
    return value


def _group_drained(group):
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    if not root.exists():
        return True
    try:
        return not any(
            child.read_text().strip() for child in root.rglob("cgroup.procs")
        )
    except OSError as exc:
        raise WatchdogError("Service cgroup drain could not be verified") from exc


def _group_processes(group):
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    if not root.exists():
        return {}
    try:
        pids = {
            int(pid)
            for child in root.rglob("cgroup.procs")
            for pid in child.read_text().splitlines()
        }
    except (OSError, ValueError) as exc:
        raise WatchdogError("Service cgroup processes could not be inspected") from exc
    identities = {}
    for pid in pids:
        try:
            identities[pid] = _start_time(pid)
        except WatchdogError:
            pass
    return identities


def _verified_close_residuals(group):
    residual = _group_processes(group)
    if not residual:
        return
    try:
        output = (
            subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "show",
                    "--property=ControlPID",
                    "--value",
                    SERVICE,
                ],
                env=ENV,
                capture_output=True,
                timeout=3,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        control = int(output)
        command = Path(f"/proc/{control}/cmdline").read_bytes().split(b"\0")
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
        raise WatchdogError("Residual service process identity is unavailable") from exc
    expected = [b"/usr/bin/python3", LIFECYCLE.encode(), b"close"]
    if (
        control not in residual
        or len(command) < 7
        or command[:3] != expected
        or command[3] != b"--hostname"
        or command[5] != b"--bind-host"
    ):
        raise WatchdogError("Residual service process is not the fixed close helper")
    for pid in residual:
        current = pid
        while current != control:
            try:
                fields = (
                    Path(f"/proc/{current}/stat").read_text().rsplit(") ", 1)[1].split()
                )
                current = int(fields[1])
            except (OSError, ValueError, IndexError) as exc:
                raise WatchdogError("Residual service ancestry is unverified") from exc
            if current <= 1 or current not in residual:
                raise WatchdogError("Unexpected process survived service SIGKILL")


def _kill_service():
    try:
        state = (
            subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "show",
                    "--property=ActiveState",
                    "--value",
                    SERVICE,
                ],
                env=ENV,
                capture_output=True,
                timeout=3,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise WatchdogError("Service state lookup failed") from exc
    if state not in ("inactive", "active", "activating", "deactivating", "failed"):
        raise WatchdogError("Unexpected service state")
    group = _service_cgroup(allow_empty=True)
    fixed_group = "/system.slice/" + SERVICE
    if group is None:
        if state in ("inactive", "failed") and _group_drained(fixed_group):
            return
        raise WatchdogError("Service cgroup is missing while service may still run")
    if state == "inactive":
        if _group_drained(group):
            return
        raise WatchdogError("Inactive service still has processes")
    occupants = _group_processes(group)
    try:
        subprocess.run(
            [
                "/usr/bin/systemctl",
                "kill",
                "--signal=SIGKILL",
                "--kill-who=all",
                SERVICE,
            ],
            env=ENV,
            capture_output=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WatchdogError("Service cgroup termination failed") from exc
    deadline = time.monotonic() + 8
    while True:
        remaining = {}
        for pid, start in occupants.items():
            try:
                if _start_time(pid) == start:
                    remaining[pid] = start
            except WatchdogError:
                pass
        if not remaining:
            break
        if time.monotonic() >= deadline:
            raise WatchdogError("Preexisting service processes survived SIGKILL")
        time.sleep(0.1)
    _verified_close_residuals(group)


def _row(db_path, run, resource, token, statuses):
    if (
        not RUN.fullmatch(run)
        or resource != "prism-m23-run-" + run
        or not RUN.fullmatch(token)
    ):
        raise WatchdogError("Invalid exact run identity")
    try:
        with closing(
            sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        ) as db:
            row = db.execute(
                "SELECT status,runtime_profile,runtime_resource,runtime_token FROM runs WHERE id=?",
                (run,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise WatchdogError("Durable run lookup failed") from exc
    if (
        row is None
        or row[0] not in statuses
        or row[1:] != ("reference-linux", resource, token)
    ):
        raise WatchdogError("Durable run identity or state did not match")


def _mark_uncertain(db_path, run, resource, token):
    try:
        with closing(sqlite3.connect(db_path, timeout=2)) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT status FROM runs WHERE id=? AND runtime_profile='reference-linux' "
                "AND runtime_resource=? AND runtime_token=?",
                (run, resource, token),
            ).fetchone()
            if previous is None or previous[0] not in (
                "queued",
                "running",
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            ):
                raise WatchdogError("Durable run changed during host cleanup")
            db.execute(
                "UPDATE runs SET status='uncertain',finished=?,result=NULL,error=? "
                "WHERE id=? AND runtime_profile='reference-linux' AND runtime_resource=? "
                "AND runtime_token=? AND status IN ('queued','running','completed','failed','cancelled','uncertain')",
                (
                    time.time(),
                    "Host watchdog stopped service after loss of a confirmed lease; owner inspection required.",
                    run,
                    resource,
                    token,
                ),
            )
            if previous[0] in ("completed", "failed", "cancelled"):
                db.execute(
                    "INSERT INTO events(at,kind,actor,resource,outcome) VALUES(?,?,?,?,?)",
                    (
                        time.time(),
                        "run_reclassified",
                        "host-watchdog",
                        run,
                        "uncertain",
                    ),
                )
            db.commit()
    except sqlite3.Error as exc:
        raise WatchdogError("Could not mark abandoned run uncertain") from exc


class WatchdogClient:
    def __init__(self, path=SOCKET):
        self.path = str(path)

    def _call(self, op, run=None, resource=None, token=None, pid=None):
        request = {"op": op}
        if run is not None:
            request.update(run=run, resource=resource, token=token)
        if pid is not None:
            request["pid"] = pid
        data = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        if len(data) > MAX_MESSAGE:
            raise WatchdogError("Watchdog request exceeds its bound")
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(1.5)
                conn.connect(self.path)
                conn.sendall(data)
                response = bytearray()
                while not response.endswith(b"\n"):
                    chunk = conn.recv(MAX_MESSAGE + 1 - len(response))
                    if not chunk or len(response) + len(chunk) > MAX_MESSAGE:
                        raise WatchdogError("Host watchdog response is invalid")
                    response.extend(chunk)
        except OSError as exc:
            raise WatchdogError("Host watchdog is unavailable") from exc
        if len(response) > MAX_MESSAGE or not response.endswith(b"\n"):
            raise WatchdogError("Host watchdog response is invalid")
        try:
            result = json.loads(response)
        except (UnicodeError, ValueError) as exc:
            raise WatchdogError("Host watchdog response is invalid") from exc
        if result != {"ok": True}:
            raise WatchdogError("Host watchdog denied the request")

    def health(self):
        self._call("health")

    def register(self, run, resource, token):
        self._call("register", run, resource, token)

    def attach(self, run, resource, token, pid):
        self._call("attach", run, resource, token, pid)

    def renew(self, run, resource, token):
        self._call("renew", run, resource, token)

    def finishing(self, run, resource, token):
        self._call("finishing", run, resource, token)

    def release(self, run, resource, token):
        self._call("release", run, resource, token)

    def abort(self, run, resource, token):
        self._call("abort", run, resource, token)


class HostWatchdog:
    def __init__(
        self, db_path, *, runtime=None, kill_service=_kill_service, clock=time.monotonic
    ):
        self.db_path = Path(db_path)
        self.runtime = runtime or ReferenceLinuxRuntime(readiness=False)
        self.kill_service = kill_service
        self.clock = clock
        self.active = None
        self.blocked = False

    def _cleanup(self, run, resource, token):
        # Repeated inspection catches a controller that creates after the first observation.
        for _ in range(3):
            self.runtime.reconcile(resource, token, settle_seconds=1)
            time.sleep(0.25)
        _mark_uncertain(self.db_path, run, resource, token)

    def startup(self):
        try:
            if self.db_path.is_symlink():
                raise WatchdogError("Durable run database is a symlink")
            rows = []
            if self.db_path.exists():
                with closing(
                    sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                ) as db:
                    rows = list(
                        db.execute(
                            "SELECT id,runtime_resource,runtime_token,status FROM runs "
                            "WHERE runtime_profile='reference-linux' AND status IN "
                            "('queued','running','completed','failed','cancelled','uncertain')"
                        )
                    )
            resources = set(self.runtime.all_resources())
            bound = {}
            for run, resource, token, status in rows:
                if not isinstance(resource, str) or not isinstance(token, str):
                    raise WatchdogError("Reference run has no exact resource identity")
                _row(self.db_path, run, resource, token, (status,))
                if resource in bound:
                    raise WatchdogError("Duplicate reference resource identity")
                bound[resource] = (run, resource, token, status)
            if resources - bound.keys():
                raise WatchdogError("Unknown reference namespace resource")
            pending = [
                row
                for row in rows
                if row[3] in ("queued", "running") or row[1] in resources
            ]
            if pending:
                self.kill_service()
            for run, resource, token, _ in pending:
                self._cleanup(run, resource, token)
            if self.runtime.all_resources():
                raise WatchdogError("Unknown reference namespace resource")
        except (OSError, sqlite3.Error, EngineError, ValueError, WatchdogError):
            self.blocked = True
            raise

    def _expire(self):
        if self.active is None:
            return
        run, resource, token = (
            self.active[key] for key in ("run", "resource", "token")
        )
        self.active = None
        try:
            try:
                _row(
                    self.db_path,
                    run,
                    resource,
                    token,
                    ("completed", "failed", "cancelled"),
                )
                if self.runtime.inspect_owned(resource, token) is None:
                    return
            except (WatchdogError, EngineError, ValueError):
                pass
            self.kill_service()
            self._cleanup(run, resource, token)
            if self.runtime.all_resources():
                raise WatchdogError("Unknown reference namespace resource")
        except (EngineError, OSError, ValueError, WatchdogError):
            self.blocked = True
            raise WatchdogError(
                "Host watchdog could not confirm service termination and cleanup"
            )

    def tick(self):
        if not self.active:
            return
        try:
            alive = (
                _start_time(self.active["service_pid"]) == self.active["service_start"]
            )
        except WatchdogError:
            alive = False
        if self.active["worker_pid"] is not None and not self.active["finishing"]:
            try:
                worker_alive = (
                    _start_time(self.active["worker_pid"])
                    == self.active["worker_start"]
                )
            except WatchdogError:
                worker_alive = False
            if worker_alive:
                self.active["worker_dead_at"] = None
            elif self.active["worker_dead_at"] is None:
                self.active["worker_dead_at"] = self.clock()
        else:
            self.active["worker_dead_at"] = None
        worker_lost = (
            self.active["worker_dead_at"] is not None
            and self.clock() - self.active["worker_dead_at"] > 2.5
        )
        if not alive or worker_lost or self.clock() > self.active["deadline"]:
            self._expire()

    def dispatch(self, request, peer_pid, peer_uid):
        if peer_uid != 0 or not isinstance(request, dict):
            raise WatchdogError("Root peer required")
        op = request.get("op")
        if op == "health" and set(request) == {"op"}:
            if self.blocked:
                raise WatchdogError("Watchdog blocked")
            return
        if self.blocked or op not in (
            "register",
            "attach",
            "renew",
            "finishing",
            "release",
            "abort",
        ):
            raise WatchdogError("Watchdog blocked or invalid request")
        expected = {"op", "run", "resource", "token"} | (
            {"pid"} if op == "attach" else set()
        )
        if set(request) != expected:
            raise WatchdogError("Invalid request fields")
        run, resource, token = (request[key] for key in ("run", "resource", "token"))
        if not all(isinstance(value, str) for value in (run, resource, token)):
            raise WatchdogError("Invalid run identity")
        _row(
            self.db_path,
            run,
            resource,
            token,
            ("queued", "running")
            if op in ("register", "attach", "renew", "finishing", "abort")
            else ("completed", "failed", "cancelled", "uncertain"),
        )
        if op == "register":
            if self.active or _cgroup(peer_pid) != _service_cgroup():
                raise WatchdogError("Unexpected service process or active run")
            self.active = {
                "run": run,
                "resource": resource,
                "token": token,
                "service_pid": peer_pid,
                "service_start": _start_time(peer_pid),
                "worker_pid": None,
                "worker_start": None,
                "worker_dead_at": None,
                "finishing": False,
                "deadline": self.clock() + LEASE_SECONDS,
            }
            return
        if not self.active or any(
            self.active[key] != request[key] for key in ("run", "resource", "token")
        ):
            raise WatchdogError("Run is not registered")
        if (
            peer_pid != self.active["service_pid"]
            or _start_time(peer_pid) != self.active["service_start"]
        ):
            raise WatchdogError("Service process identity changed")
        if op == "attach":
            pid = request["pid"]
            if (
                self.active["worker_pid"] is not None
                or type(pid) is not int
                or pid <= 1
                or _cgroup(pid) != _service_cgroup()
            ):
                raise WatchdogError("Worker is outside service cgroup")
            self.active["worker_pid"] = pid
            self.active["worker_start"] = _start_time(pid)
        elif op == "renew":
            if self.active["worker_pid"] is None or self.active["finishing"]:
                raise WatchdogError("Worker is not attached")
            if _start_time(self.active["worker_pid"]) != self.active["worker_start"]:
                raise WatchdogError("Worker process identity changed")
        elif op == "finishing":
            if self.active["worker_pid"] is None or self.active["finishing"]:
                raise WatchdogError("Worker finish was not expected")
            self.active["finishing"] = True
        elif op == "release":
            if self.active["worker_pid"] is not None and not self.active["finishing"]:
                raise WatchdogError("Attached worker did not confirm trusted finish")
            if self.runtime.inspect_owned(resource, token) is not None:
                raise WatchdogError("Exact resource remains")
            self.active = None
            return
        elif op == "abort":
            self._expire()
            if self.blocked:
                raise WatchdogError("Abort cleanup was not confirmed")
            return
        self.active["deadline"] = self.clock() + LEASE_SECONDS

    def serve(self, path=SOCKET):
        if os.geteuid() != 0:
            raise WatchdogError("Root is required")
        self.startup()
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.parent.lstat()
        if (
            info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o700
            or not stat.S_ISDIR(info.st_mode)
        ):
            raise WatchdogError("Private socket directory is unsafe")
        if path.exists() or path.is_symlink():
            raise WatchdogError("Socket path already exists")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(path))
            os.chmod(path, 0o600)
            listener.listen(4)
            listener.settimeout(0.1)
            while True:
                self.tick()
                try:
                    conn, _ = listener.accept()
                except TimeoutError:
                    continue
                with conn:
                    conn.settimeout(1)
                    try:
                        pid, uid, _ = struct.unpack(
                            "3i",
                            conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
                        )
                        payload = bytearray()
                        while not payload.endswith(b"\n"):
                            chunk = conn.recv(MAX_MESSAGE + 1 - len(payload))
                            if not chunk or len(payload) + len(chunk) > MAX_MESSAGE:
                                raise WatchdogError("Invalid bounded request")
                            payload.extend(chunk)
                        request = _strict_json(payload)
                        self.dispatch(request, pid, uid)
                        conn.sendall(b'{"ok":true}\n')
                    except (
                        WatchdogError,
                        ValueError,
                        TypeError,
                        KeyError,
                        OSError,
                        EngineError,
                    ):
                        try:
                            conn.sendall(b'{"ok":false}\n')
                        except OSError:
                            pass
                        if self.blocked:
                            raise WatchdogError("Host watchdog entered fail-stop state")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    serve = actions.add_parser("serve")
    serve.add_argument("--db", required=True, type=Path)
    actions.add_parser("health")
    args = parser.parse_args(argv)
    try:
        if args.action == "health":
            WatchdogClient().health()
        else:
            HostWatchdog(args.db).serve()
    except (WatchdogError, EngineError, OSError, sqlite3.Error) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
