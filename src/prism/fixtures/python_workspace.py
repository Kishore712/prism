"""Trusted guest launcher. Project output is untrusted, even after JSON collection."""

import base64
import hashlib
import json
import os
import resource
import stat
import subprocess
import sys
from pathlib import Path

policy = json.loads(base64.b64decode(sys.argv[1], validate=True))
root = Path("/project")
scratch = Path("/scratch")
output_names = {f["name"] for f in policy["outputs"]}
# Link approved readonly material into the familiar relative project layout.
for name in policy["inputs"]:
    target = scratch / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(root / name)
for name in output_names:
    (scratch / name).parent.mkdir(parents=True, exist_ok=True)


def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024, 128 * 1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def read_logs():
    logs = {}
    for name in ("stdout", "stderr"):
        fd = os.open("/scratch/." + name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
                raise ValueError("Output log limit")
            logs[name] = stream.read(4097).decode("utf-8", errors="replace")
    return logs


with open("/scratch/.stdout", "xb") as out, open("/scratch/.stderr", "xb") as err:
    child = subprocess.run(
        [sys.executable, "-I", "-B", str(root / policy["entrypoint"])],
        cwd=scratch,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=err,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": "/nonexistent",
        },
        preexec_fn=limits,
        check=False,
    )
if child.returncode:
    # Failure remains nonzero; expose only bounded scoped logs for diagnosis.
    logs = read_logs()
    print(logs["stdout"], end="", flush=True)
    print(logs["stderr"], end="", file=sys.stderr, flush=True)
    raise SystemExit(1)
# Walk using dirfds and NOFOLLOW: neither parent symlinks nor special files
# can turn declared artifact collection into an arbitrary file read.
collected = []
for item in policy["outputs"]:
    fd = os.open("/scratch", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = item["name"].split("/")
        for part in parts[:-1]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
        result_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        with os.fdopen(result_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > 32768
            ):
                raise ValueError("Invalid artifact")
            raw = stream.read(32769)
            if len(raw) > 32768:
                raise ValueError("Oversized artifact")
        text = raw.decode("utf-8")
        if not isinstance(json.loads(text), (dict, list)):
            raise TypeError("Invalid JSON artifact")
        collected.append(
            {**item, "text": text, "sha256": hashlib.sha256(raw).hexdigest()}
        )
    finally:
        os.close(fd)
logs = read_logs()
print(
    json.dumps({"action": "python-workspace", "files": collected, **logs}), flush=True
)
