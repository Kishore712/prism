#!/usr/bin/env python3
"""Package and install only Prism's pinned synthetic document-handoff fixture."""

import argparse
import ctypes
import errno
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "examples/document-handoff"
PROJECT_ROOT = Path("/var/lib/prism-identity/project")
OWNER_UID = 0
FILE_HASHES = {
    ".prism-project.json": "ccbca1855265c4f26b0e80cfdc68469120f36b4e1c7b2c4e10ce36cef4d8dc7e",
    "README.md": "7b515d4b3a25719f20fea966fa78b50cdaf6c6ac5f048c45e1c65c7474e5af44",
    "config/release.json": "751144f3e65c4499341d3b56540d35bde8e5d0221956427a45588dfa5371df4b",
    "docs/release.md": "7184635a68f82316ffd39960d60905a8b9c6e9092a273e276198ce8100f7ca79",
    "private/notes.txt": "0a4471e80064770e43963be1b6236340dcace4da30283c1bc4d458f76bad55e4",
    "unlisted-canary.txt": "4b5a9db4ee48e834ab21a6f2b723c61e7c7b23e4c5d5a66f2f6d3e1086afafe7",
}
DIRECTORIES = frozenset({"config", "docs", "private"})
INVENTORY = [[name, FILE_HASHES[name]] for name in sorted(FILE_HASHES)]
PROJECT_SHA256 = "737348aaef852355c5a16381fafc96f0c6df0931edafddc93a77906d064db525"
MAX_FILES = 16
MAX_BYTES = 128 * 1024


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def inventory_sha256():
    encoded = json.dumps(INVENTORY, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return sha256(encoded)


def checked_contents(root):
    """Read the exact pinned fixture without following links or hard links."""
    if inventory_sha256() != PROJECT_SHA256:
        raise ValueError("Pinned project inventory is inconsistent.")
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("Fixture root is not a directory.")
    found_dirs = set()
    found_files = {}

    def walk(directory, prefix=""):
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    found_dirs.add(relative)
                    walk(Path(directory) / entry.name, relative + "/")
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    if relative not in FILE_HASHES or info.st_size > MAX_BYTES:
                        raise ValueError("Unexpected or oversized fixture file.")
                    fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        before = os.fstat(fd)
                        data = os.read(fd, MAX_BYTES + 1)
                        after = os.fstat(fd)
                    finally:
                        os.close(fd)
                    if (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    ) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ) or len(data) != before.st_size:
                        raise ValueError("Fixture file changed during packaging.")
                    found_files[relative] = data
                else:
                    raise ValueError("Fixture contains a link or unsupported entry.")

    walk(root)
    if found_dirs != DIRECTORIES or set(found_files) != set(FILE_HASHES):
        raise ValueError("Fixture inventory differs from the pinned project.")
    if len(found_files) > MAX_FILES or sum(map(len, found_files.values())) > MAX_BYTES:
        raise ValueError("Fixture exceeds project limits.")
    if any(sha256(data) != FILE_HASHES[name] for name, data in found_files.items()):
        raise ValueError("Fixture bytes differ from the pinned project.")
    return found_files


def package(output):
    contents = checked_contents(FIXTURE)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with (
            os.fdopen(fd, "wb") as stream,
            tarfile.open(
                fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT
            ) as archive,
        ):
            for name in sorted(DIRECTORIES | set(contents)):
                directory = name in DIRECTORIES
                entry = tarfile.TarInfo(name + ("/" if directory else ""))
                entry.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
                entry.mode = 0o700 if directory else 0o600
                entry.uid = entry.gid = entry.mtime = 0
                entry.size = 0 if directory else len(contents[name])
                archive.addfile(
                    entry, None if directory else io.BytesIO(contents[name])
                )
        return sha256(Path(output).read_bytes())
    except BaseException:
        Path(output).unlink(missing_ok=True)
        raise


def checked_archive(path, expected_sha256):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected a lowercase SHA-256 transfer digest.")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > 256 * 1024
    ):
        raise ValueError("Transfer must be one bounded regular file.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > 256 * 1024
            or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise ValueError("Transfer changed type after opening.")
        raw = os.read(fd, 256 * 1024 + 1)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (
        len(raw) != before.st_size
        or (before.st_dev, before.st_ino, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_mtime_ns)
        or sha256(raw) != expected_sha256
    ):
        raise ValueError("Transfer bytes changed or hash mismatch.")
    contents = {}
    found_dirs = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for entry in archive:
            name = entry.name.removesuffix("/")
            if name in DIRECTORIES and entry.isdir():
                if name in found_dirs or entry.mode != 0o700:
                    raise ValueError("Duplicate or invalid project directory.")
                found_dirs.add(name)
            elif name in FILE_HASHES and entry.isfile():
                if name in contents or entry.mode != 0o600 or entry.size > MAX_BYTES:
                    raise ValueError("Duplicate or invalid project file.")
                contents[name] = archive.extractfile(entry).read()
            else:
                raise ValueError("Unexpected project archive member.")
            if entry.uid != 0 or entry.gid != 0:
                raise ValueError("Project archive ownership is invalid.")
    if found_dirs != DIRECTORIES or set(contents) != set(FILE_HASHES):
        raise ValueError("Project archive has an extra or missing entry.")
    if sum(map(len, contents.values())) > MAX_BYTES:
        raise ValueError("Project archive is too large.")
    if any(sha256(data) != FILE_HASHES[name] for name, data in contents.items()):
        raise ValueError("Project archive contains changed file bytes.")
    return contents


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_no_replace(source, destination):
    """Publish a complete directory atomically, failing if target exists."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        args = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(source), os.fsencode(destination), 0x00000004)
    else:
        raise ValueError("No atomic no-overwrite directory rename on this platform.")
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(destination))


def install(transfer, expected_sha256):
    if os.geteuid() != OWNER_UID:
        raise ValueError("Project installation requires root.")
    contents = checked_archive(Path(transfer), expected_sha256)
    for ancestor in reversed((PROJECT_ROOT.parent, *PROJECT_ROOT.parent.parents)):
        info = ancestor.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in (0, OWNER_UID)
            or (
                info.st_mode & 0o022
                and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            )
        ):
            raise ValueError("Project installation parent is not root controlled.")
    if PROJECT_ROOT.exists() or PROJECT_ROOT.is_symlink():
        raise FileExistsError(
            errno.EEXIST, "Project is already installed", str(PROJECT_ROOT)
        )
    stage = Path(tempfile.mkdtemp(prefix=".project-stage-", dir=PROJECT_ROOT.parent))
    try:
        stage.chmod(0o700)
        for name in sorted(DIRECTORIES):
            directory = stage / name
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        for name, data in sorted(contents.items()):
            path = stage / name
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        if checked_contents(stage) != contents:
            raise ValueError("Installed project verification failed.")
        for name in sorted(DIRECTORIES):
            fsync_directory(stage / name)
        fsync_directory(stage)
        fsync_directory(PROJECT_ROOT.parent)
        rename_no_replace(stage, PROJECT_ROOT)
        fsync_directory(PROJECT_ROOT.parent)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return PROJECT_SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    pack = actions.add_parser("package")
    pack.add_argument("output", type=Path)
    deploy = actions.add_parser("install")
    deploy.add_argument("transfer", type=Path)
    deploy.add_argument("--transfer-sha256", required=True)
    args = parser.parse_args()
    if args.action == "package":
        print(f"transfer_sha256={package(args.output)}")
        print(f"project_sha256={PROJECT_SHA256}")
    else:
        print(f"project_sha256={install(args.transfer, args.transfer_sha256)}")


if __name__ == "__main__":
    main()
