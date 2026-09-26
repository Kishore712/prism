#!/usr/bin/env python3
"""Offline, root-only resolution of one inspected reference-Linux uncertain run.

This helper is installed separately from the pinned application release. It never
starts a service, removes a resource, or claims that the abandoned work succeeded.
"""

import argparse
import fcntl
import ipaddress
import json
import os
import re
import shlex
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

DB = Path("/var/lib/prism-identity/service-state/demo.sqlite")
SERVICE = "prism-identity-service.service"
WATCHDOG = "prism-identity-host-watchdog.service"
WATCHDOG_SOCKET = Path("/run/prism-host-watchdog/watchdog.sock")
UNIT_ROOT = Path("/etc/systemd/system")
LIFECYCLE = "/usr/local/libexec/prism-identity-service-lifecycle.py"
GUEST = "/usr/local/libexec/prism-identity-service-guest.py"
TRANSFER = "eb9e409032696fe4b6159cc2d338e548e6c1100d723a45b4b0607549f80bd501"
SOURCE = "1f09da7467b1d76b20ad82ee039e906cd0f18befa359fd851e61d1bf437954dd"
UPDATE = "/var/lib/prism/identity-pilot/service-updates/" + TRANSFER + "/installed.json"
RELEASE = "/var/lib/prism/identity-pilot/app-releases/service-" + TRANSFER
NERDCTL = "/usr/local/bin/nerdctl"
CONTAINERD_SOCKET = "/run/containerd/containerd.sock"
NAMESPACE = "prism-m0"
RUN = re.compile(r"[0-9a-f]{32}\Z")
HOST = re.compile(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\Z")
OWNER_UID = 0
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"}
ERROR = "Owner resolved an uncertain reference run after offline host inspection; execution outcome is unknown."
MAX_OUTPUT = 65536


class ResolutionDenied(RuntimeError):
    pass


def command(argv, *, limit=MAX_OUTPUT):
    try:
        completed = subprocess.run(
            argv, env=ENV, capture_output=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResolutionDenied("A required host check failed") from exc
    if len(completed.stdout) + len(completed.stderr) > limit:
        raise ResolutionDenied("A host check exceeded its output bound")
    return completed.stdout


def systemd(unit, field):
    raw = command(
        ["/usr/bin/systemctl", "show", "--property=" + field, "--value", unit],
        limit=4096,
    )
    try:
        return raw.decode("ascii").strip()
    except UnicodeError as exc:
        raise ResolutionDenied("Invalid service state") from exc


def unit_pin(unit):
    path = UNIT_ROOT / unit
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or info.st_size > 8192
        ):
            raise ResolutionDenied("Installed service unit is not root controlled")
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError) as exc:
        raise ResolutionDenied("Installed service unit cannot be inspected") from exc
    if (
        systemd(unit, "FragmentPath") != str(path)
        or systemd(unit, "NeedDaemonReload") != "no"
        or systemd(unit, "DropInPaths") != ""
    ):
        raise ResolutionDenied("Loaded service unit differs from its installed file")
    if any(line.endswith("\\") or ";" in line for line in lines):
        raise ResolutionDenied("Installed unit contains an ambiguous command")
    return lines


def setting(lines, name, value):
    return [line.partition("=")[2] for line in lines if line.startswith(name + "=")] == [value]


def unit_command(lines, directive, action, *, watchdog=False):
    matches = [line.partition("=")[2] for line in lines
               if line.startswith(directive + "=")
               and line.partition("=")[2].startswith("/usr/bin/python3 " + LIFECYCLE + " " + action + " ")]
    if len(matches) != 1:
        raise ResolutionDenied("Installed unit lacks one fixed lifecycle command")
    try:
        argv = shlex.split(matches[0])
    except ValueError as exc:
        raise ResolutionDenied("Installed unit command is invalid") from exc
    if argv[:3] != ["/usr/bin/python3", LIFECYCLE, action]:
        raise ResolutionDenied("Installed lifecycle command differs")
    expected = {"--installed-record": UPDATE, "--bundle-sha256": SOURCE}
    if watchdog:
        expected.update({"--release": RELEASE, "--db": str(DB)})
    for option, value in expected.items():
        if argv.count(option) != 1 or argv[argv.index(option) + 1:argv.index(option) + 2] != [value]:
            raise ResolutionDenied("Installed unit release identity differs")
    return argv


def option(argv, name):
    if argv.count(name) != 1:
        raise ResolutionDenied("Installed lifecycle identity is ambiguous")
    index = argv.index(name)
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        raise ResolutionDenied("Installed lifecycle identity is incomplete")
    return argv[index + 1]


def close_command(lines, host, bind):
    matches = [line.partition("=")[2] for line in lines
               if line.startswith("ExecStopPost=")]
    if len(matches) != 1:
        raise ResolutionDenied("Installed unit lacks one fixed close hook")
    try:
        argv = shlex.split(matches[0])
    except ValueError as exc:
        raise ResolutionDenied("Installed close hook is invalid") from exc
    if argv != ["/usr/bin/python3", LIFECYCLE, "close", "--hostname", host,
                "--bind-host", bind]:
        raise ResolutionDenied("Installed close hook identity differs")


def exec_directives(lines, expected):
    found = {}
    for line in lines:
        if line.startswith("Exec"):
            name = line.partition("=")[0]
            found[name] = found.get(name, 0) + 1
    if found != expected:
        raise ResolutionDenied("Installed unit executable directives differ")


def installed_units_pinned():
    service = unit_pin(SERVICE)
    watchdog = unit_pin(WATCHDOG)
    binding = "tailscaled.service " + WATCHDOG
    if (
        not setting(service, "BindsTo", binding)
        or not setting(service, "Requires", "tailscaled.service prism-identify-boot-restore.service " + WATCHDOG)
        or WATCHDOG not in systemd(SERVICE, "BindsTo").split()
        or not setting(service, "User", "root")
        or not setting(service, "Restart", "no")
        or not setting(watchdog, "User", "root")
        or not setting(watchdog, "Restart", "on-failure")
        or not setting(watchdog, "RuntimeDirectory", "prism-host-watchdog")
        or not setting(watchdog, "RuntimeDirectoryMode", "0700")
    ):
        raise ResolutionDenied("Installed private service guard differs")
    exec_directives(service, {"ExecStartPre": 3, "ExecStart": 1,
                              "ExecStartPost": 1, "ExecStopPost": 1})
    exec_directives(watchdog, {"ExecStartPre": 1, "ExecStart": 1,
                               "ExecStopPost": 1})
    prepare = unit_command(service, "ExecStartPre", "prepare")
    host, bind, run_id = (option(prepare, name) for name in
                          ("--hostname", "--bind-host", "--run-id"))
    try:
        valid_ip = ipaddress.ip_address(bind) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        valid_ip = False
    if not HOST.fullmatch(host) or not valid_ip or not RUN.fullmatch(run_id):
        raise ResolutionDenied("Installed private host identity differs")
    for lines, directive, action, is_watchdog in (
        (service, "ExecStartPre", "watchdog-ready", False),
        (service, "ExecStart", "serve", False),
        (service, "ExecStartPost", "open", False),
        (watchdog, "ExecStartPre", "watchdog-prepare", True),
        (watchdog, "ExecStart", "watchdog-serve", True),
    ):
        argv = unit_command(lines, directive, action, watchdog=is_watchdog)
        if (option(argv, "--hostname"), option(argv, "--bind-host"),
            option(argv, "--run-id")) != (host, bind, run_id):
            raise ResolutionDenied("Installed lifecycle host identity differs")
    close_command(service, host, bind)
    close_command(watchdog, host, bind)
    if (
        not any(line.startswith("ExecStartPre=/usr/bin/python3 " + GUEST + " preflight ") for line in service)
    ):
        raise ResolutionDenied("Installed service startup commands differ")


def service_stopped():
    if systemd(SERVICE, "ActiveState") not in ("inactive", "failed"):
        raise ResolutionDenied("Identity service is not stopped")
    if systemd(SERVICE, "MainPID") != "0" or systemd(SERVICE, "ControlPID") != "0":
        raise ResolutionDenied("Identity service still has a process")
    group = systemd(SERVICE, "ControlGroup")
    fixed = "/system.slice/" + SERVICE
    if group not in ("", fixed):
        raise ResolutionDenied("Identity service cgroup is unexpected")
    root = Path("/sys/fs/cgroup") / fixed.lstrip("/")
    if root.exists():
        try:
            if any(p.read_text().strip() for p in root.rglob("cgroup.procs")):
                raise ResolutionDenied("Identity service cgroup is occupied")
        except OSError as exc:
            raise ResolutionDenied("Identity service cgroup cannot be inspected") from exc


def tailnet_closed():
    try:
        status = json.loads(command(["/usr/bin/tailscale", "status", "--json"]))
        prefs = json.loads(command(["/usr/bin/tailscale", "debug", "prefs"]))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ResolutionDenied("Private tailnet state is invalid") from exc
    own = status.get("Self") if isinstance(status, dict) else None
    if (
        not isinstance(own, dict)
        or status.get("BackendState") != "Running"
        or own.get("Online") is not True
        or own.get("Tags") != ["tag:prism-host"]
        or not isinstance(prefs, dict)
        or prefs.get("ShieldsUp") is not True
        or prefs.get("RunSSH") is not False
        or prefs.get("RouteAll") is not False
        or prefs.get("AdvertiseRoutes")
        or prefs.get("ExitNodeID")
        or prefs.get("ExitNodeIP")
    ):
        raise ResolutionDenied("Private tailnet Shields Up is not confirmed")


def watchdog_healthy():
    if systemd(WATCHDOG, "ActiveState") != "active" or systemd(WATCHDOG, "SubState") != "running":
        raise ResolutionDenied("Independent host watchdog is not active")
    try:
        if int(systemd(WATCHDOG, "MainPID")) <= 1:
            raise ResolutionDenied("Independent host watchdog has no main process")
        with socket.socket(socket.AF_UNIX) as peer:
            peer.settimeout(2)
            peer.connect(str(WATCHDOG_SOCKET))
            peer.sendall(b'{"op":"health"}\n')
            answer = peer.recv(1025)
        if answer != b'{"ok":true}\n' and answer != b'{"ok": true}\n':
            raise ResolutionDenied("Independent host watchdog denied health")
    except (OSError, ValueError) as exc:
        raise ResolutionDenied("Independent host watchdog health is unavailable") from exc


def database_identity():
    path = DB
    try:
        parent = path.parent.lstat()
        info = path.lstat()
    except OSError as exc:
        raise ResolutionDenied("Fixed database path is unavailable") from exc
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != OWNER_UID
        or parent.st_mode & 0o077
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != OWNER_UID
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise ResolutionDenied("Fixed database is not a private root-owned regular file")
    return info.st_dev, info.st_ino


@contextmanager
def service_lock():
    path = DB.with_name("server.lock")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise ResolutionDenied("Fixed service lock is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ResolutionDenied("Fixed service lock is not private and stable")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResolutionDenied("Identity service lock is occupied") from exc
        yield
    except OSError as exc:
        raise ResolutionDenied("Fixed service lock could not be verified") from exc
    finally:
        os.close(descriptor)


def open_database():
    identity = database_identity()
    db = None
    try:
        db = sqlite3.connect(str(DB), timeout=2)
        db.row_factory = sqlite3.Row
        if database_identity() != identity:
            raise ResolutionDenied("Fixed database changed during opening")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA foreign_keys=ON")
        return db, identity
    except ResolutionDenied:
        if db is not None:
            db.close()
        raise
    except (OSError, sqlite3.Error):
        if db is not None:
            db.close()
        raise ResolutionDenied("Fixed database could not be opened") from None


def row_checks(db, run):
    active = db.execute(
        "SELECT count(*) FROM runs WHERE status IN ('queued','running')"
    ).fetchone()[0]
    if active:
        raise ResolutionDenied("Queued or running work remains")
    rows = db.execute(
        "SELECT id,status,runtime_profile,runtime_resource,runtime_token,finished,result,error "
        "FROM runs WHERE id=?", (run,)
    ).fetchall()
    if len(rows) != 1:
        raise ResolutionDenied("Exact run is missing or ambiguous")
    row = rows[0]
    resource = "prism-m23-run-" + run
    token = row["runtime_token"]
    if (
        row["status"] != "uncertain"
        or row["runtime_profile"] != "reference-linux"
        or row["runtime_resource"] != resource
        or not isinstance(token, str)
        or not RUN.fullmatch(token)
        or row["result"] is not None
    ):
        raise ResolutionDenied("Exact run is not a bound uncertain reference run")
    collision = db.execute(
        "SELECT count(*) FROM runs WHERE id<>? AND "
        "(runtime_resource=? OR runtime_token=?)", (run, resource, token)
    ).fetchone()[0]
    if collision:
        raise ResolutionDenied("Reference resource identity is shared")
    return row


def namespace_empty():
    output = command([
        NERDCTL, "--address", CONTAINERD_SOCKET, "--namespace", NAMESPACE,
        "ps", "-a", "--format", "{{.ID}}",
    ])
    try:
        if output.decode("utf-8").strip():
            raise ResolutionDenied("Dedicated reference namespace is not empty")
    except UnicodeError as exc:
        raise ResolutionDenied("Reference namespace listing is invalid") from exc


def no_matching_process(row):
    needles = tuple(
        value.encode("ascii")
        for value in (row["runtime_resource"], row["runtime_token"])
    )
    try:
        processes = list(Path("/proc").iterdir())
    except OSError as exc:
        raise ResolutionDenied("Host process list is unavailable") from exc
    for process in processes:
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            args = (process / "cmdline").read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ResolutionDenied("A host process could not be inspected") from exc
        if any(needle in args for needle in needles):
            raise ResolutionDenied("A process still matches the exact run identity")


def inspect_conditions(db, run):
    installed_units_pinned()
    service_stopped()
    tailnet_closed()
    watchdog_healthy()
    row = row_checks(db, run)
    namespace_empty()
    no_matching_process(row)
    installed_units_pinned()
    service_stopped()
    tailnet_closed()
    watchdog_healthy()
    return row


def _execute_locked(action, run):
    if os.geteuid() != 0:
        raise ResolutionDenied("Root is required")
    if not RUN.fullmatch(run):
        raise ResolutionDenied("Expected one exact 32-character run ID")
    db, identity = open_database()
    try:
        if action == "inspect":
            db.execute("BEGIN")
            inspect_conditions(db, run)
            db.rollback()
            return {"run": run, "ready": True, "action": "inspect", "changed": False}
        if action != "resolve":
            raise ResolutionDenied("Unknown action")
        db.execute("BEGIN IMMEDIATE")
        row = inspect_conditions(db, run)
        if database_identity() != identity:
            raise ResolutionDenied("Fixed database changed during resolution")
        original = json.dumps(
            {"from": "uncertain", "to": "failed", "original_finished": row["finished"],
             "original_error": row["error"], "original_result": row["result"]},
            separators=(",", ":"), ensure_ascii=True,
        )
        changed = db.execute(
            "UPDATE runs SET status='failed',result=NULL,error=? WHERE id=? "
            "AND status='uncertain' AND runtime_profile='reference-linux' "
            "AND runtime_resource=? AND runtime_token=? AND result IS NULL",
            (ERROR, run, row["runtime_resource"], row["runtime_token"]),
        ).rowcount
        if changed != 1:
            raise ResolutionDenied("Exact run changed during resolution")
        db.execute(
            "INSERT INTO events(at,kind,actor,resource,outcome) VALUES(?,?,?,?,?)",
            (time.time(), "run_uncertain_resolved", "local-root-operator", run, original),
        )
        db.commit()
        return {"run": run, "ready": True, "action": "resolve", "changed": True,
                "status": "failed", "execution_outcome": "unknown"}
    except (ResolutionDenied, sqlite3.Error):
        db.rollback()
        raise
    finally:
        db.close()


def execute(action, run):
    if os.geteuid() != 0:
        raise ResolutionDenied("Root is required")
    if not RUN.fullmatch(run):
        raise ResolutionDenied("Expected one exact 32-character run ID")
    with service_lock():
        return _execute_locked(action, run)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "resolve"))
    parser.add_argument("run", help="exact reference run ID")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(execute(args.action, args.run), sort_keys=True))
    except (ResolutionDenied, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, ResolutionDenied) else "Database transaction failed"
        print(json.dumps({"ready": False, "changed": False, "error": message}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
