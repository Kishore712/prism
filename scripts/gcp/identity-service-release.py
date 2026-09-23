#!/usr/bin/env python3
"""Build a separate private Prism release from a verified one-boot baseline."""

import argparse
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

BASE = Path("/var/lib/prism/identity-pilot")
BASELINE = BASE / "oneboot/installed.json"
LIFECYCLE = Path("/usr/local/libexec/prism-identity-service-lifecycle.py")
UPDATE_ROOT = BASE / "service-updates"
RELEASE_ROOT = BASE / "app-releases"
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
PATCH_NAME = "src/prism/webapp.py"
APP_JS_NAME = "src/prism/static/app.js"
IDENTITY_NAME = "src/prism/identity.py"
PATCH_LIMITS = {
    PATCH_NAME: 16 * 1024 * 1024,
    APP_JS_NAME: 2 * 1024 * 1024,
    IDENTITY_NAME: 16 * 1024 * 1024,
}
PATCH_SETS = (
    frozenset((PATCH_NAME,)),
    frozenset((PATCH_NAME, APP_JS_NAME)),
    frozenset((PATCH_NAME, APP_JS_NAME, IDENTITY_NAME)),
)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def regular(path, maximum=None):
    info = Path(path).lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (maximum is not None and info.st_size > maximum)
    ):
        raise ValueError("Expected a bounded regular file.")
    return info


def private_directory(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("Expected a root-owned private directory.")


def transfer_bytes(data, app_js=None, identity=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        files = {PATCH_NAME: data}
        if app_js is not None:
            files[APP_JS_NAME] = app_js
        if identity is not None:
            if app_js is None:
                raise ValueError("Identity update requires the fixed three-file patch.")
            files[IDENTITY_NAME] = identity
        for name in sorted(files):
            entry = tarfile.TarInfo(name)
            entry.size = len(files[name])
            entry.mode = 0o600
            entry.mtime = 0
            entry.uid = entry.gid = 0
            archive.addfile(entry, io.BytesIO(files[name]))
    return buffer.getvalue()


def package(args):
    source = Path(args.webapp)
    regular(source, 16 * 1024 * 1024)
    target = Path(args.output)
    if not target.is_absolute() or target.exists() or target.is_symlink():
        raise ValueError("Transfer tar must use a new absolute path.")
    data = source.read_bytes()
    app_js_path = getattr(args, "app_js", None)
    identity_path = getattr(args, "identity", None)
    if identity_path is not None and app_js_path is None:
        raise ValueError("Identity update requires --app-js.")
    app_js = None
    if app_js_path is not None:
        regular(app_js_path, PATCH_LIMITS[APP_JS_NAME])
        app_js = Path(app_js_path).read_bytes()
    identity = None
    if identity_path is not None:
        regular(identity_path, PATCH_LIMITS[IDENTITY_NAME])
        identity = Path(identity_path).read_bytes()
    with target.open("xb") as raw:
        os.fchmod(raw.fileno(), 0o600)
        raw.write(transfer_bytes(data, app_js, identity))
    report = {
        "transfer_tar_sha256": digest(target),
        "webapp_sha256": hashlib.sha256(data).hexdigest(),
    }
    if app_js is not None:
        report["kind"] = (
            "prism_service_update_v3"
            if identity is not None
            else "prism_service_update_v2"
        )
        report["app_js_sha256"] = hashlib.sha256(app_js).hexdigest()
    if identity is not None:
        report["identity_sha256"] = hashlib.sha256(identity).hexdigest()
    print(json.dumps(report, sort_keys=True))


def read_patches(tar_path, expected_tar, expected_hashes):
    if (
        frozenset(expected_hashes) not in PATCH_SETS
        or not HEX64.fullmatch(expected_tar)
        or any(not HEX64.fullmatch(value) for value in expected_hashes.values())
    ):
        raise ValueError("Expected fixed SHA-256 digests.")
    maximum = sum(PATCH_LIMITS[name] for name in expected_hashes) + 20480
    regular(tar_path, maximum)
    if digest(tar_path) != expected_tar:
        raise ValueError("Transfer tar hash mismatch.")
    raw = Path(tar_path).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) != len(expected_hashes) or {m.name for m in members} != set(
            expected_hashes
        ):
            if set(expected_hashes) == {PATCH_NAME}:
                raise ValueError(
                    "Transfer tar must contain only the regular webapp source."
                )
            raise ValueError("Transfer tar must contain only the fixed patch sources.")
        files = {}
        for member in members:
            if (
                not member.isfile()
                or member.size > PATCH_LIMITS[member.name]
                or member.pax_headers
            ):
                raise ValueError(
                    "Transfer tar must contain only regular bounded sources."
                )
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Transfer tar has no source bytes.")
            files[member.name] = stream.read(PATCH_LIMITS[member.name] + 1)
    if any(
        hashlib.sha256(files[name]).hexdigest() != expected_hashes[name]
        for name in expected_hashes
    ):
        raise ValueError("Transferred patch hash mismatch.")
    if raw != transfer_bytes(
        files[PATCH_NAME], files.get(APP_JS_NAME), files.get(IDENTITY_NAME)
    ):
        raise ValueError("Transfer tar contains noncanonical or extra bytes.")
    return files


def read_patch(tar_path, expected_tar, expected_webapp):
    return read_patches(tar_path, expected_tar, {PATCH_NAME: expected_webapp})[
        PATCH_NAME
    ]


def load_lifecycle(path=LIFECYCLE):
    info = regular(path, 1024 * 1024)
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Lifecycle helper is not root controlled.")
    spec = importlib.util.spec_from_file_location("prism_identity_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_runtime(source, target, old_release, new_release):
    """Copy Python and venv bytes without linking mutable old release content."""
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Baseline runtime directory is invalid.")
    shutil.copytree(source, target, symlinks=True)
    for current, directories, files in os.walk(target, followlinks=False):
        for name in directories + files:
            path = Path(current) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raw = os.readlink(path)
                old_link = source / path.relative_to(target)
                resolved = (
                    (old_link.parent / raw).resolve()
                    if not os.path.isabs(raw)
                    else Path(raw).resolve()
                )
                try:
                    relative = resolved.relative_to(old_release)
                except ValueError as exc:
                    raise ValueError(
                        "Runtime link escapes the baseline release."
                    ) from exc
                if os.path.isabs(raw):
                    path.unlink()
                    path.symlink_to(new_release / relative)
            elif stat.S_ISDIR(info.st_mode):
                path.chmod(0o700)
            elif stat.S_ISREG(info.st_mode):
                path.chmod(0o700 if info.st_mode & 0o111 else 0o600)
            else:
                raise ValueError("Runtime contains an unsupported file type.")
    cfg = target / "pyvenv.cfg"
    if cfg.is_file():
        value = cfg.read_text()
        cfg.write_text(value.replace(str(old_release), str(new_release)))
        cfg.chmod(0o600)


def source_tar(release, files, target):
    with target.open("xb") as raw:
        os.fchmod(raw.fileno(), 0o600)
        with tarfile.open(
            fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT
        ) as archive:
            for name in sorted(files):
                data = (release / name).read_bytes()
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                entry.mode = 0o600
                entry.mtime = 0
                entry.uid = entry.gid = 0
                archive.addfile(entry, io.BytesIO(data))
    return digest(target)


def validate_runtime_links(release):
    for name in ("python", "venv"):
        for current, directories, files in os.walk(release / name, followlinks=False):
            for entry in directories + files:
                candidate = Path(current) / entry
                if candidate.is_symlink():
                    try:
                        candidate.resolve(strict=True).relative_to(release)
                    except (OSError, ValueError) as exc:
                        raise ValueError(
                            "Copied runtime link escapes the new release."
                        ) from exc


def assert_service_stopped(lifecycle):
    output = lifecycle.run(
        "systemctl",
        "show",
        "--property=ActiveState",
        "--property=SubState",
        "--property=MainPID",
        "--property=ControlPID",
        "prism-identity-service.service",
    ).stdout
    lines = output.splitlines()
    values = {}
    for line in lines:
        if line.count("=") != 1:
            raise ValueError("Stop the Prism service before updating.")
        key, value = line.split("=", 1)
        if key in values:
            raise ValueError("Stop the Prism service before updating.")
        values[key] = value
    if values != {
        "ActiveState": "inactive",
        "SubState": "dead",
        "MainPID": "0",
        "ControlPID": "0",
    }:
        raise ValueError("Stop the Prism service before updating.")


def tailnet_ipv4(addresses):
    if not isinstance(addresses, list):
        raise TypeError("Expected one fixed tailnet IPv4 address.")
    selected = []
    for value in addresses:
        if not isinstance(value, str):
            raise TypeError("Invalid tailnet address.")
        address = ipaddress.ip_address(value)
        if address.version == 4:
            if address not in ipaddress.ip_network("100.64.0.0/10"):
                raise ValueError("Invalid tailnet IPv4 address.")
            selected.append(str(address))
    if len(selected) != 1:
        raise ValueError("Expected one fixed tailnet IPv4 address.")
    return selected[0]


def install(args):
    if os.geteuid() != 0:
        raise ValueError("Guest update requires root.")
    lifecycle = load_lifecycle()
    lifecycle.checked_directory(BASE)
    lifecycle.checked_directory(RELEASE_ROOT)
    old_release_text, base_bundle, base_run = lifecycle.installation(BASELINE)
    old_release = Path(old_release_text)
    old_manifest = json.loads((BASE / "oneboot/manifest.json").read_text())
    transfer_path = Path(args.transfer_tar)
    if not transfer_path.is_absolute():
        raise ValueError("Transfer tar must use an absolute path.")
    lifecycle.checked_directory(transfer_path.parent)
    private_directory(transfer_path.parent)
    app_js_sha = getattr(args, "app_js_sha256", None)
    identity_sha = getattr(args, "identity_sha256", None)
    if identity_sha is not None and app_js_sha is None:
        raise ValueError("Identity update requires --app-js-sha256.")
    expected_hashes = {PATCH_NAME: args.webapp_sha256}
    if app_js_sha is not None:
        expected_hashes[APP_JS_NAME] = app_js_sha
    if identity_sha is not None:
        expected_hashes[IDENTITY_NAME] = identity_sha
    transfer_info = regular(
        transfer_path,
        sum(PATCH_LIMITS[name] for name in expected_hashes) + 20480,
    )
    if transfer_info.st_uid != 0 or transfer_info.st_mode & 0o077:
        raise ValueError("Transfer tar must be a private root-owned file.")
    patches = read_patches(args.transfer_tar, args.transfer_tar_sha256, expected_hashes)
    # Service and inbound tailnet must already be stopped/closed by the operator.
    lifecycle.run("systemctl", "is-active", "--quiet", "tailscaled.service")
    assert_service_stopped(lifecycle)
    host = lifecycle.fixed_host()
    status = json.loads(lifecycle.run("tailscale", "status", "--json").stdout)
    own = status.get("Self") or {}
    ip = tailnet_ipv4(own.get("TailscaleIPs"))
    if own.get("DNSName", "").lower().rstrip(".") != host:
        raise ValueError("Fixed tailnet identity is unavailable.")
    lifecycle.tailnet_state(host, ip, True)

    release_name = "service-" + args.transfer_tar_sha256
    new_release = RELEASE_ROOT / release_name
    update_dir = UPDATE_ROOT / args.transfer_tar_sha256
    if (
        new_release.exists()
        or new_release.is_symlink()
        or update_dir.exists()
        or update_dir.is_symlink()
    ):
        raise ValueError("This service update already exists.")
    UPDATE_ROOT.mkdir(mode=0o700, exist_ok=True)
    lifecycle.checked_directory(UPDATE_ROOT)
    private_directory(UPDATE_ROOT)
    stage = Path(tempfile.mkdtemp(prefix=".service-stage-", dir=RELEASE_ROOT))
    stage.chmod(0o700)
    record_stage = Path(tempfile.mkdtemp(prefix=".record-stage-", dir=UPDATE_ROOT))
    record_stage.chmod(0o700)
    published_release = published_record = False
    try:
        for name in sorted(lifecycle.SOURCE_FILES):
            destination = stage / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = (
                patches[name] if name in patches else (old_release / name).read_bytes()
            )
            with destination.open("xb") as output:
                output.write(data)
            destination.chmod(0o600)
        for name in ("python", "venv"):
            copy_runtime(old_release / name, stage / name, old_release, new_release)
        hashes = {name: digest(stage / name) for name in lifecycle.SOURCE_FILES}
        for name in lifecycle.SOURCE_FILES - set(patches):
            if hashes[name] != old_manifest["file_sha256"][name]:
                raise ValueError("A baseline source byte changed during copy.")
        for name, expected in expected_hashes.items():
            if hashes[name] != expected:
                raise ValueError("Patched source bytes changed during copy.")
        tar_hash = source_tar(
            stage, lifecycle.SOURCE_FILES, record_stage / "source.tar"
        )
        record = {
            "kind": (
                "prism_service_update_v3"
                if identity_sha is not None
                else (
                    "prism_service_update_v2"
                    if app_js_sha is not None
                    else "prism_service_update_v1"
                )
            ),
            "base_bundle_sha256": base_bundle,
            "base_run_id": base_run,
            "transfer_tar_sha256": args.transfer_tar_sha256,
            "source_tar_sha256": tar_hash,
            "webapp_sha256": args.webapp_sha256,
            "file_sha256": hashes,
            "release_name": release_name,
        }
        if app_js_sha is not None:
            record["app_js_sha256"] = app_js_sha
        if identity_sha is not None:
            record["identity_sha256"] = identity_sha
        for name, value in (
            ("manifest.json", record),
            (
                "installed.json",
                {
                    "source_tar_sha256": tar_hash,
                    "transfer_tar_sha256": args.transfer_tar_sha256,
                    "base_bundle_sha256": base_bundle,
                    "base_run_id": base_run,
                },
            ),
        ):
            target = record_stage / name
            with target.open("x") as output:
                json.dump(value, output, sort_keys=True)
                output.write("\n")
            target.chmod(0o600)
        assert_service_stopped(lifecycle)
        lifecycle.tailnet_state(host, ip, True)
        stage.rename(new_release)
        published_release = True
        record_stage.rename(update_dir)
        published_record = True
        validate_runtime_links(new_release)
        # Independently import through the copied Python environment.
        probe = (
            "import json, pathlib, sys, prism, authlib, fastapi, uvicorn; "
            "print(json.dumps({'prism':str(pathlib.Path(prism.__file__).resolve()),"
            "'executable':str(pathlib.Path(sys.executable).resolve()),"
            "'base_prefix':str(pathlib.Path(sys.base_prefix).resolve())}))"
        )
        command = [str(new_release / "venv/bin/python"), "-c", probe]
        result = subprocess.run(
            command,
            cwd=new_release,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(new_release / "src")},
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        paths = json.loads(result.stdout)
        if (
            not Path(paths["prism"]).is_relative_to(new_release / "src")
            or not Path(paths["executable"]).is_relative_to(new_release)
            or not Path(paths["base_prefix"]).is_relative_to(new_release)
        ):
            raise ValueError("Copied runtime import check failed.")
        lifecycle.installation(update_dir / "installed.json", tar_hash, base_run)
        print(
            json.dumps(
                {
                    "update_record": str(update_dir / "installed.json"),
                    "source_tar_sha256": tar_hash,
                    "transfer_tar_sha256": args.transfer_tar_sha256,
                },
                sort_keys=True,
            )
        )
    except BaseException:
        if published_record:
            shutil.rmtree(update_dir)
        else:
            shutil.rmtree(record_stage, ignore_errors=True)
        if published_release:
            shutil.rmtree(new_release)
        else:
            shutil.rmtree(stage, ignore_errors=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    build = actions.add_parser("package")
    build.add_argument("--webapp", required=True)
    build.add_argument("--app-js")
    build.add_argument("--identity")
    build.add_argument("--output", required=True)
    deploy = actions.add_parser("install")
    deploy.add_argument("--transfer-tar", required=True)
    deploy.add_argument("--transfer-tar-sha256", required=True)
    deploy.add_argument("--webapp-sha256", required=True)
    deploy.add_argument("--app-js-sha256")
    deploy.add_argument("--identity-sha256")
    args = parser.parse_args(argv)
    try:
        (package if args.action == "package" else install)(args)
    except (
        OSError,
        ValueError,
        TypeError,
        tarfile.TarError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Prism release update failed: {type(exc).__name__}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
