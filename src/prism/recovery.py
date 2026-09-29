"""Durable, single-use offline recovery event and locked workload checks.

This module is part of the immutable application release.  Host lifecycle code
may import it only after verifying that release's source manifest.
"""

import fcntl
import json
import os
import re
import sqlite3
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path("/var/lib/prism-identity/offline-recovery")
EPISODE = ROOT / "episode"
AUDIT = ROOT / "audit.jsonl"
STATE_LOCK = ROOT / "state.lock"
DB = Path("/var/lib/prism-identity/service-state/demo.sqlite")
LOCK = DB.with_name("server.lock")
NERDCTL = "/usr/local/bin/nerdctl"
SOCKET = "/run/containerd/containerd.sock"
INVOCATION = re.compile(r"[0-9a-f]{32}\Z")
EVENT_ID = re.compile(r"[0-9a-f]{32}\Z")
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"}


class RecoveryDenied(RuntimeError):
    """A fixed, secret-free reason to deny automatic recovery."""


def _private_file(path, maximum):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or info.st_mode & 0o077
        or info.st_size > maximum
    ):
        raise RecoveryDenied("A private recovery file is invalid")
    return info


def directory():
    parent = ROOT.parent.lstat()
    info = ROOT.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_mode & 0o022
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RecoveryDenied("Recovery directory is not root private")


def _sync_directory():
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def episode():
    directory()
    _private_file(EPISODE, 512)
    raw = EPISODE.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise RecoveryDenied("Recovery event is invalid") from exc
    keys = {"version", "event_id", "category"}
    state = value.get("state") if isinstance(value, dict) else None
    if state is not None:
        keys |= {"state"}
    if state in ("attempted", "entered") or (
        state == "failed" and isinstance(value, dict) and "invocation_id" in value
    ):
        keys |= {"invocation_id"}
    controller_keys = {
        "controller_pid",
        "controller_start",
        "controller_invocation",
        "boot_id",
        "deadline_ns",
    }
    if isinstance(value, dict) and "controller_pid" in value:
        keys |= controller_keys
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or type(value.get("version")) is not int
        or value["version"] != 1
        or value.get("category") != "offline"
        or not isinstance(value.get("event_id"), str)
        or not EVENT_ID.fullmatch(value["event_id"])
        or state not in (None, "consumed", "ready", "attempted", "entered", "failed")
        or (
            "invocation_id" in keys
            and (
                not isinstance(value.get("invocation_id"), str)
                or not INVOCATION.fullmatch(value["invocation_id"])
            )
        )
        or (
            controller_keys <= keys
            and (
                type(value.get("controller_pid")) is not int
                or value["controller_pid"] <= 1
                or type(value.get("controller_start")) is not int
                or value["controller_start"] <= 0
                or not isinstance(value.get("controller_invocation"), str)
                or not INVOCATION.fullmatch(value["controller_invocation"])
                or not isinstance(value.get("boot_id"), str)
                or not re.fullmatch(r"[0-9a-f-]{36}", value["boot_id"])
                or type(value.get("deadline_ns")) is not int
                or value["deadline_ns"] <= 0
            )
        )
    ):
        raise RecoveryDenied("Recovery event is invalid")
    return value


def _atomic(path, data):
    directory()
    name = ROOT / (".write-" + os.urandom(16).hex())
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync_directory()
    except BaseException:
        name.unlink(missing_ok=True)
        raise


@contextmanager
def event_lock():
    directory()
    fd = os.open(
        STATE_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        info = os.fstat(fd)
        current = STATE_LOCK.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise RecoveryDenied("Recovery state lock is invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryDenied("Recovery state lock is occupied") from exc
        yield
    finally:
        os.close(fd)


def _transition_unlocked(expected, target, invocation=None):
    value = episode()
    if value.get("state") != expected:
        raise RecoveryDenied("Recovery event is not in the required state")
    if target in ("attempted", "entered") or invocation is not None:
        if not isinstance(invocation, str) or not INVOCATION.fullmatch(invocation):
            raise RecoveryDenied("Service invocation identity is unavailable")
        if value.get("invocation_id", invocation) != invocation:
            raise RecoveryDenied("Service invocation identity changed")
        value["invocation_id"] = invocation
    value["state"] = target
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _atomic(EPISODE, data)
    return value


def transition(expected, target, invocation=None):
    with event_lock():
        return _transition_unlocked(expected, target, invocation)


def _boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", value):
        raise RecoveryDenied("Host boot identity is invalid")
    return value


def _process_start(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text()
    fields = raw.rsplit(")", 1)[1].split()
    # Field 22 (starttime) is zero-index 19 after fields 1 and 2 are removed.
    return int(fields[19])


def arm_ready(controller_invocation):
    if not isinstance(controller_invocation, str) or not INVOCATION.fullmatch(
        controller_invocation
    ):
        raise RecoveryDenied("Recovery controller invocation is unavailable")
    with event_lock():
        value = episode()
        if value.get("state") != "consumed":
            raise RecoveryDenied("Recovery event is not consumed")
        value.update(
            {
                "state": "ready",
                "controller_pid": os.getpid(),
                "controller_start": _process_start(os.getpid()),
                "controller_invocation": controller_invocation,
                "boot_id": _boot_id(),
                "deadline_ns": time.monotonic_ns() + 30_000_000_000,
            }
        )
        _atomic(
            EPISODE,
            (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        return value


def claim_ready(invocation, controller_invocation):
    with event_lock():
        value = episode()
        if value.get("state") != "ready":
            raise RecoveryDenied("Recovery event is not ready")
        if (
            value["controller_invocation"] != controller_invocation
            or value["boot_id"] != _boot_id()
            or time.monotonic_ns() > value["deadline_ns"]
            or _process_start(value["controller_pid"]) != value["controller_start"]
        ):
            raise RecoveryDenied("Recovery controller authorization expired")
        return _transition_unlocked("ready", "attempted", invocation)


def audit(event, event_id):
    if event not in {
        "seen",
        "sample",
        "checked",
        "consumed",
        "watchdog",
        "rechecked",
        "attempted",
        "entered",
        "opened",
        "verified",
        "failed",
        "archive-intent",
        "abandon-intent",
    }:
        raise RecoveryDenied("Unknown audit event")
    directory()
    if AUDIT.exists() or AUDIT.is_symlink():
        _private_file(AUDIT, 1024 * 1024)
    data = (
        json.dumps({"event": event, "event_id": event_id}, sort_keys=True) + "\n"
    ).encode()
    fd = os.open(AUDIT, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise RecoveryDenied("Recovery audit write failed")
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_directory()


@contextmanager
def service_lock():
    parent = LOCK.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o077:
        raise RecoveryDenied("Service state directory is not private")
    fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        current = LOCK.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise RecoveryDenied("Service lock is not private and stable")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryDenied("Service lock is occupied") from exc
        yield fd
    finally:
        os.close(fd)


def workload_clear():
    _private_file(DB, 256 * 1024 * 1024)
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=2)
    try:
        db.execute("PRAGMA trusted_schema=OFF")
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RecoveryDenied("Service database integrity failed")
        if db.execute(
            "SELECT count(*) FROM runs WHERE status IN ('queued','running','uncertain')"
        ).fetchone() != (0,):
            raise RecoveryDenied("Pending or uncertain work remains")
    except sqlite3.Error as exc:
        raise RecoveryDenied("Service database cannot be checked") from exc
    finally:
        db.close()
    result = subprocess.run(
        [
            NERDCTL,
            "--address",
            SOCKET,
            "--namespace",
            "prism-m0",
            "ps",
            "-a",
            "--format",
            "{{.Names}}",
        ],
        env=ENV,
        capture_output=True,
        timeout=10,
        check=True,
    )
    if result.stderr or result.stdout.strip():
        raise RecoveryDenied("Reference namespace is not empty")


def archive(event_id):
    with event_lock():
        _archive_unlocked(event_id)


def abandon(event_id):
    with event_lock():
        value = episode()
        if value.get("state") != "failed" or value["event_id"] != event_id:
            raise RecoveryDenied("Only a failed event can be abandoned")
        _rename_episode("abandoned-" + event_id)


def _archive_unlocked(event_id):
    value = episode()
    if value.get("state") != "entered" or value["event_id"] != event_id:
        raise RecoveryDenied("Recovery event cannot be archived")
    _rename_episode("archived-" + event_id)


def _rename_episode(name):
    destination = ROOT / name
    if destination.exists() or destination.is_symlink():
        raise RecoveryDenied("Recovery event was already archived")
    EPISODE.rename(destination)
    try:
        _sync_directory()
    except OSError:
        try:
            destination.rename(EPISODE)
            _sync_directory()
        except OSError as exc:
            raise RecoveryDenied("Recovery archive rollback failed") from exc
        raise
