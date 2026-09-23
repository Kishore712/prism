"""Offline tests for the immutable private identity-service release update."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_tool = load(
    "identity_release", ROOT / "scripts/gcp/identity-service-release.py"
)
life = load(
    "identity_lifecycle_update", ROOT / "scripts/gcp/identity-service-lifecycle.py"
)


class ImmutableReleaseTests(unittest.TestCase):
    def test_v3_install_stages_exactly_three_patched_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            boot = base / "oneboot"
            boot.mkdir(mode=0o700)
            app = base / "app-releases"
            app.mkdir(mode=0o700)
            updates = base / "service-updates"
            updates.mkdir(mode=0o700)
            old = app / ("a" * 64 + "-" + "b" * 32)
            old.mkdir(mode=0o700)
            hashes = {}
            for name in life.SOURCE_FILES:
                source = old / name
                source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source.write_bytes(name.encode())
                source.chmod(0o600)
                hashes[name] = hashlib.sha256(name.encode()).hexdigest()
            for name in ("python", "venv"):
                (old / name).mkdir(mode=0o700)
            (boot / "manifest.json").write_text(json.dumps({"file_sha256": hashes}))
            sources = {}
            for name, content in (
                ("webapp", b"new webapp"),
                ("app_js", b"new app js"),
                ("identity", b"new identity"),
            ):
                source = base / name
                source.write_bytes(content)
                sources[name] = source
            transfer = base / "patch.tar"
            release_tool.package(
                SimpleNamespace(
                    **{name: str(path) for name, path in sources.items()},
                    output=str(transfer),
                )
            )
            transfer_sha = release_tool.digest(transfer)
            args = SimpleNamespace(
                transfer_tar=str(transfer),
                transfer_tar_sha256=transfer_sha,
                **{
                    f"{name}_sha256": release_tool.digest(path)
                    for name, path in sources.items()
                },
            )

            def verify_stage(stage, files, target):
                self.assertEqual(
                    (stage / release_tool.PATCH_NAME).read_bytes(), b"new webapp"
                )
                self.assertEqual(
                    (stage / release_tool.APP_JS_NAME).read_bytes(), b"new app js"
                )
                self.assertEqual(
                    (stage / release_tool.IDENTITY_NAME).read_bytes(), b"new identity"
                )
                self.assertEqual(
                    (stage / "src/prism/cli.py").read_bytes(), b"src/prism/cli.py"
                )
                raise RuntimeError("stage checked")

            with (
                self.assertRaisesRegex(RuntimeError, "stage checked"),
                patch.object(release_tool, "BASE", base),
                patch.object(release_tool, "BASELINE", boot / "installed.json"),
                patch.object(release_tool, "RELEASE_ROOT", app),
                patch.object(release_tool, "UPDATE_ROOT", updates),
                patch.object(release_tool, "load_lifecycle", return_value=life),
                patch.object(release_tool.os, "geteuid", return_value=0),
                patch.object(release_tool, "private_directory"),
                patch.object(
                    release_tool,
                    "regular",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o100600),
                ),
                patch.object(life, "checked_directory"),
                patch.object(
                    life, "installation", return_value=(str(old), "a" * 64, "b" * 32)
                ),
                patch.object(
                    life,
                    "run",
                    return_value=SimpleNamespace(
                        stdout=json.dumps(
                            {
                                "Self": {
                                    "TailscaleIPs": ["100.100.100.100"],
                                    "DNSName": "pilot.example.ts.net",
                                }
                            }
                        )
                    ),
                ),
                patch.object(life, "fixed_host", return_value="pilot.example.ts.net"),
                patch.object(life, "tailnet_state"),
                patch.object(release_tool, "assert_service_stopped"),
                patch.object(release_tool, "source_tar", side_effect=verify_stage),
            ):
                release_tool.install(args)
            self.assertFalse((app / ("service-" + transfer_sha)).exists())
            self.assertFalse((updates / transfer_sha).exists())

    def test_v2_install_copies_exactly_two_patched_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            boot = base / "oneboot"
            boot.mkdir(mode=0o700)
            app = base / "app-releases"
            app.mkdir(mode=0o700)
            updates = base / "service-updates"
            updates.mkdir(mode=0o700)
            old = app / ("a" * 64 + "-" + "b" * 32)
            old.mkdir(mode=0o700)
            hashes = {}
            for name in life.SOURCE_FILES:
                source = old / name
                source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source.write_bytes(name.encode())
                source.chmod(0o600)
                hashes[name] = hashlib.sha256(name.encode()).hexdigest()
            for name in ("python", "venv"):
                (old / name).mkdir(mode=0o700)
            (boot / "manifest.json").write_text(json.dumps({"file_sha256": hashes}))
            webapp = base / "webapp.py"
            app_js = base / "app.js"
            webapp.write_bytes(b"new webapp")
            app_js.write_bytes(b"new app js")
            transfer = base / "patch.tar"
            release_tool.package(
                SimpleNamespace(
                    webapp=str(webapp),
                    app_js=str(app_js),
                    output=str(transfer),
                )
            )
            transfer_sha = release_tool.digest(transfer)
            args = SimpleNamespace(
                transfer_tar=str(transfer),
                transfer_tar_sha256=transfer_sha,
                webapp_sha256=release_tool.digest(webapp),
                app_js_sha256=release_tool.digest(app_js),
            )

            def verify_stage(stage, files, target):
                self.assertEqual(
                    (stage / release_tool.PATCH_NAME).read_bytes(), b"new webapp"
                )
                self.assertEqual(
                    (stage / release_tool.APP_JS_NAME).read_bytes(), b"new app js"
                )
                self.assertEqual(
                    (stage / "src/prism/cli.py").read_bytes(), b"src/prism/cli.py"
                )
                raise RuntimeError("stage checked")

            with (
                self.assertRaisesRegex(RuntimeError, "stage checked"),
                patch.object(release_tool, "BASE", base),
                patch.object(release_tool, "BASELINE", boot / "installed.json"),
                patch.object(release_tool, "RELEASE_ROOT", app),
                patch.object(release_tool, "UPDATE_ROOT", updates),
                patch.object(release_tool, "load_lifecycle", return_value=life),
                patch.object(release_tool.os, "geteuid", return_value=0),
                patch.object(release_tool, "private_directory"),
                patch.object(
                    release_tool,
                    "regular",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o100600),
                ),
                patch.object(life, "checked_directory"),
                patch.object(
                    life, "installation", return_value=(str(old), "a" * 64, "b" * 32)
                ),
                patch.object(
                    life,
                    "run",
                    return_value=SimpleNamespace(
                        stdout=json.dumps(
                            {
                                "Self": {
                                    "TailscaleIPs": ["100.100.100.100"],
                                    "DNSName": "pilot.example.ts.net",
                                }
                            }
                        )
                    ),
                ),
                patch.object(life, "fixed_host", return_value="pilot.example.ts.net"),
                patch.object(life, "tailnet_state"),
                patch.object(release_tool, "assert_service_stopped"),
                patch.object(release_tool, "source_tar", side_effect=verify_stage),
            ):
                release_tool.install(args)
            self.assertFalse((app / ("service-" + transfer_sha)).exists())
            self.assertFalse((updates / transfer_sha).exists())
            self.assertEqual(
                (old / release_tool.APP_JS_NAME).read_bytes(),
                release_tool.APP_JS_NAME.encode(),
            )

    def test_failed_runtime_check_removes_only_new_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            boot = base / "oneboot"
            boot.mkdir(mode=0o700)
            app = base / "app-releases"
            app.mkdir(mode=0o700)
            updates = base / "service-updates"
            updates.mkdir(mode=0o700)
            old = app / ("a" * 64 + "-" + "b" * 32)
            old.mkdir(mode=0o700)
            hashes = {}
            for name in life.SOURCE_FILES:
                source = old / name
                source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source.write_bytes(name.encode())
                source.chmod(0o600)
                hashes[name] = hashlib.sha256(name.encode()).hexdigest()
            (old / "python").mkdir(mode=0o700)
            (old / "venv/bin").mkdir(mode=0o700, parents=True)
            interpreter = old / "venv/bin/python"
            interpreter.write_bytes(b"fake-python")
            interpreter.chmod(0o700)
            (boot / "manifest.json").write_text(json.dumps({"file_sha256": hashes}))
            patch_file = base / "webapp.py"
            patch_file.write_bytes(b"patched webapp")
            transfer = base / "transfer.tar"
            release_tool.package(
                SimpleNamespace(webapp=str(patch_file), output=str(transfer))
            )
            transfer_sha = release_tool.digest(transfer)
            webapp_sha = hashlib.sha256(patch_file.read_bytes()).hexdigest()
            update_release = app / ("service-" + transfer_sha)
            update_record = updates / transfer_sha
            args = SimpleNamespace(
                transfer_tar=str(transfer),
                transfer_tar_sha256=transfer_sha,
                webapp_sha256=webapp_sha,
            )

            def command(argv, **kwargs):
                if argv[0] == "systemctl":
                    return SimpleNamespace(returncode=1)
                raise subprocess.CalledProcessError(1, argv)

            with (
                self.assertRaises(subprocess.CalledProcessError),
                patch.object(release_tool, "BASE", base),
                patch.object(release_tool, "BASELINE", boot / "installed.json"),
                patch.object(release_tool, "RELEASE_ROOT", app),
                patch.object(release_tool, "UPDATE_ROOT", updates),
                patch.object(release_tool, "load_lifecycle", return_value=life),
                patch.object(release_tool.os, "geteuid", return_value=0),
                patch.object(release_tool, "private_directory"),
                patch.object(
                    release_tool,
                    "regular",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o100600),
                ),
                patch.object(life, "checked_directory"),
                patch.object(
                    life, "installation", return_value=(str(old), "a" * 64, "b" * 32)
                ),
                patch.object(
                    life,
                    "run",
                    return_value=SimpleNamespace(
                        stdout=json.dumps(
                            {
                                "Self": {
                                    "TailscaleIPs": [
                                        "100.100.100.100",
                                        "fd7a:115c:a1e0::1",
                                    ],
                                    "DNSName": "pilot.example.ts.net",
                                }
                            }
                        )
                    ),
                ),
                patch.object(life, "fixed_host", return_value="pilot.example.ts.net"),
                patch.object(life, "tailnet_state"),
                patch.object(release_tool, "assert_service_stopped"),
                patch.object(release_tool.subprocess, "run", side_effect=command),
            ):
                release_tool.install(args)
            self.assertFalse(update_release.exists())
            self.assertFalse(update_record.exists())
            self.assertEqual(
                (old / release_tool.PATCH_NAME).read_bytes(),
                release_tool.PATCH_NAME.encode(),
            )
            with (
                self.assertRaisesRegex(ValueError, "Stop the Prism service"),
                patch.object(release_tool, "BASE", base),
                patch.object(release_tool, "BASELINE", boot / "installed.json"),
                patch.object(release_tool, "RELEASE_ROOT", app),
                patch.object(release_tool, "UPDATE_ROOT", updates),
                patch.object(release_tool, "load_lifecycle", return_value=life),
                patch.object(release_tool.os, "geteuid", return_value=0),
                patch.object(release_tool, "private_directory"),
                patch.object(
                    release_tool,
                    "regular",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o100600),
                ),
                patch.object(life, "checked_directory"),
                patch.object(
                    life, "installation", return_value=(str(old), "a" * 64, "b" * 32)
                ),
                patch.object(life, "run", return_value=SimpleNamespace(stdout="")),
                patch.object(
                    release_tool,
                    "assert_service_stopped",
                    side_effect=ValueError("Stop the Prism service before updating."),
                ),
            ):
                release_tool.install(args)
            self.assertFalse(update_release.exists())
            self.assertFalse(update_record.exists())

            states = iter(
                (
                    "ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n",
                    "ActiveState=activating\nSubState=start-post\nMainPID=12\nControlPID=13\n",
                )
            )

            def guest_run(*argv, **kwargs):
                if argv[:2] == ("systemctl", "show"):
                    return SimpleNamespace(stdout=next(states))
                if argv[:2] == ("tailscale", "status"):
                    return SimpleNamespace(
                        stdout=json.dumps(
                            {
                                "Self": {
                                    "TailscaleIPs": [
                                        "100.100.100.100",
                                        "fd7a:115c:a1e0::1",
                                    ],
                                    "DNSName": "pilot.example.ts.net",
                                }
                            }
                        )
                    )
                return SimpleNamespace(stdout="")

            with (
                self.assertRaisesRegex(ValueError, "Stop the Prism service"),
                patch.object(release_tool, "BASE", base),
                patch.object(release_tool, "BASELINE", boot / "installed.json"),
                patch.object(release_tool, "RELEASE_ROOT", app),
                patch.object(release_tool, "UPDATE_ROOT", updates),
                patch.object(release_tool, "load_lifecycle", return_value=life),
                patch.object(release_tool.os, "geteuid", return_value=0),
                patch.object(release_tool, "private_directory"),
                patch.object(
                    release_tool,
                    "regular",
                    return_value=SimpleNamespace(st_uid=0, st_mode=0o100600),
                ),
                patch.object(life, "checked_directory"),
                patch.object(
                    life, "installation", return_value=(str(old), "a" * 64, "b" * 32)
                ),
                patch.object(life, "run", side_effect=guest_run),
                patch.object(life, "fixed_host", return_value="pilot.example.ts.net"),
                patch.object(life, "tailnet_state"),
            ):
                release_tool.install(args)
            self.assertFalse(update_release.exists())
            self.assertFalse(update_record.exists())

    def test_service_must_be_exactly_inactive_dead_with_no_processes(self):
        healthy = "ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n"
        for state in (
            healthy.replace("inactive", "activating").replace("dead", "start-post"),
            healthy.replace("inactive", "deactivating").replace("dead", "stop-post"),
            healthy.replace("MainPID=0", "MainPID=42"),
            healthy.replace("ControlPID=0", "ControlPID=42"),
            healthy.replace("SubState=dead\n", ""),
        ):
            with (
                self.subTest(state=state),
                self.assertRaisesRegex(ValueError, "Stop the Prism service"),
            ):
                release_tool.assert_service_stopped(
                    SimpleNamespace(
                        run=lambda *args, state=state: SimpleNamespace(stdout=state)
                    )
                )
        release_tool.assert_service_stopped(
            SimpleNamespace(run=lambda *args: SimpleNamespace(stdout=healthy))
        )

    def test_one_tailnet_ipv4_allows_ipv6_but_rejects_invalid_or_multiple_ipv4(self):
        self.assertEqual(
            release_tool.tailnet_ipv4(["100.100.100.100", "fd7a:115c:a1e0::1"]),
            "100.100.100.100",
        )
        for addresses in (
            ["fd7a:115c:a1e0::1"],
            ["100.100.100.100", "100.100.100.101"],
            ["100.100.100.100", "8.8.8.8"],
            ["100.100.100.100", "invalid"],
        ):
            with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                release_tool.tailnet_ipv4(addresses)

    def test_transfer_is_canonical_single_regular_webapp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            webapp = root / "webapp.py"
            webapp.write_bytes(b"patched webapp\n")
            transfer = root / "patch.tar"
            release_tool.package(
                SimpleNamespace(webapp=str(webapp), output=str(transfer))
            )
            tar_hash = release_tool.digest(transfer)
            webapp_hash = hashlib.sha256(webapp.read_bytes()).hexdigest()
            self.assertEqual(
                release_tool.read_patch(transfer, tar_hash, webapp_hash),
                webapp.read_bytes(),
            )
            transfer.write_bytes(transfer.read_bytes() + b"extra")
            with self.assertRaisesRegex(ValueError, "noncanonical"):
                release_tool.read_patch(
                    transfer, release_tool.digest(transfer), webapp_hash
                )
            with tarfile.open(transfer, "w") as archive:
                for name in (release_tool.PATCH_NAME, "extra.txt"):
                    data = b"patched webapp\n"
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, "only the regular webapp"):
                release_tool.read_patch(
                    transfer, release_tool.digest(transfer), webapp_hash
                )

    def test_two_file_transfer_requires_exact_canonical_members_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            webapp = root / "webapp.py"
            app_js = root / "app.js"
            webapp.write_bytes(b"fixed webapp\n")
            app_js.write_bytes(b"fixed interface\n")
            transfer = root / "patch.tar"
            release_tool.package(
                SimpleNamespace(
                    webapp=str(webapp), app_js=str(app_js), output=str(transfer)
                )
            )
            hashes = {
                release_tool.PATCH_NAME: hashlib.sha256(
                    webapp.read_bytes()
                ).hexdigest(),
                release_tool.APP_JS_NAME: hashlib.sha256(
                    app_js.read_bytes()
                ).hexdigest(),
            }
            self.assertEqual(
                release_tool.read_patches(
                    transfer, release_tool.digest(transfer), hashes
                ),
                {
                    release_tool.PATCH_NAME: webapp.read_bytes(),
                    release_tool.APP_JS_NAME: app_js.read_bytes(),
                },
            )
            with self.assertRaisesRegex(ValueError, "patch hash mismatch"):
                release_tool.read_patches(
                    transfer,
                    release_tool.digest(transfer),
                    {**hashes, release_tool.APP_JS_NAME: "a" * 64},
                )
            transfer.write_bytes(transfer.read_bytes() + b"extra")
            with self.assertRaisesRegex(ValueError, "noncanonical"):
                release_tool.read_patches(
                    transfer, release_tool.digest(transfer), hashes
                )
            with tarfile.open(transfer, "w") as archive:
                for name in (
                    release_tool.PATCH_NAME,
                    release_tool.APP_JS_NAME,
                    "extra.txt",
                ):
                    data = b"fixed bytes"
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, "fixed patch sources"):
                release_tool.read_patches(
                    transfer, release_tool.digest(transfer), hashes
                )

    def test_three_file_transfer_requires_exact_canonical_members_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            files = {
                release_tool.PATCH_NAME: root / "webapp.py",
                release_tool.APP_JS_NAME: root / "app.js",
                release_tool.IDENTITY_NAME: root / "identity.py",
            }
            for name, path in files.items():
                path.write_bytes(name.encode())
            transfer = root / "patch.tar"
            with self.assertRaisesRegex(ValueError, "requires --app-js"):
                release_tool.package(
                    SimpleNamespace(
                        webapp=str(files[release_tool.PATCH_NAME]),
                        identity=str(files[release_tool.IDENTITY_NAME]),
                        output=str(transfer),
                    )
                )
            self.assertFalse(transfer.exists())
            release_tool.package(
                SimpleNamespace(
                    webapp=str(files[release_tool.PATCH_NAME]),
                    app_js=str(files[release_tool.APP_JS_NAME]),
                    identity=str(files[release_tool.IDENTITY_NAME]),
                    output=str(transfer),
                )
            )
            hashes = {name: release_tool.digest(path) for name, path in files.items()}
            self.assertEqual(
                release_tool.read_patches(
                    transfer, release_tool.digest(transfer), hashes
                ),
                {name: path.read_bytes() for name, path in files.items()},
            )
            with self.assertRaisesRegex(ValueError, "fixed SHA-256"):
                release_tool.read_patches(
                    transfer,
                    release_tool.digest(transfer),
                    {
                        release_tool.PATCH_NAME: hashes[release_tool.PATCH_NAME],
                        release_tool.IDENTITY_NAME: hashes[release_tool.IDENTITY_NAME],
                    },
                )
            with self.assertRaisesRegex(ValueError, "patch hash mismatch"):
                release_tool.read_patches(
                    transfer,
                    release_tool.digest(transfer),
                    {**hashes, release_tool.IDENTITY_NAME: "a" * 64},
                )
            transfer.write_bytes(transfer.read_bytes() + b"extra")
            with self.assertRaisesRegex(ValueError, "noncanonical"):
                release_tool.read_patches(
                    transfer, release_tool.digest(transfer), hashes
                )
            with tarfile.open(transfer, "w") as archive:
                for name in (*files, "extra.txt"):
                    data = b"fixed bytes"
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    archive.addfile(item, io.BytesIO(data))
            with self.assertRaisesRegex(ValueError, "fixed patch sources"):
                release_tool.read_patches(
                    transfer, release_tool.digest(transfer), hashes
                )

    def test_runtime_copy_relocates_only_internal_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old = root / "old"
            new = root / "new"
            source = old / "venv"
            (source / "bin").mkdir(parents=True)
            (old / "python/bin").mkdir(parents=True)
            (old / "python/bin/python3").write_bytes(b"python")
            (source / "bin/python").symlink_to(old / "python/bin/python3")
            (source / "pyvenv.cfg").write_text(f"home = {old}/python/bin\n")
            release_tool.copy_runtime(source, root / "staged", old, new)
            self.assertEqual(
                os.readlink(root / "staged/bin/python"), str(new / "python/bin/python3")
            )
            self.assertIn(str(new), (root / "staged/pyvenv.cfg").read_text())
            (source / "bin/python").unlink()
            (source / "bin/python").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(ValueError, "escapes"):
                release_tool.copy_runtime(source, root / "staged-escape", old, new)

    def test_update_record_pins_real_tar_and_complete_source_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            boot = base / "oneboot"
            boot.mkdir(mode=0o700)
            updates = base / "service-updates"
            updates.mkdir(mode=0o700)
            app = base / "app-releases"
            app.mkdir(mode=0o700)
            old_bundle, run_id, transfer = "a" * 64, "b" * 32, "c" * 64
            old = app / f"{old_bundle}-{run_id}"
            new = app / f"service-{transfer}"
            old.mkdir(mode=0o700)
            new.mkdir(mode=0o700)
            old_hashes = {}
            new_hashes = {}
            for name in life.SOURCE_FILES:
                data = name.encode()
                changed = b"fixed webapp" if name == release_tool.PATCH_NAME else data
                for directory, content in ((old, data), (new, changed)):
                    path = directory / name
                    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    path.write_bytes(content)
                    path.chmod(0o600)
                old_hashes[name] = hashlib.sha256(data).hexdigest()
                new_hashes[name] = hashlib.sha256(changed).hexdigest()

            def write(path, value):
                path.write_text(json.dumps(value))
                path.chmod(0o600)

            write(
                boot / "installed.json", {"bundle_sha256": old_bundle, "run_id": run_id}
            )
            write(
                boot / "ledger.json",
                {"run_id": run_id, "next_seq": 6, "complete": True},
            )
            write(
                boot / "manifest.json",
                {
                    "kind": "prism_oneboot_app_v1",
                    "run_id": run_id,
                    "bundle_sha256": old_bundle,
                    "raw_sha256": old_bundle,
                    "file_sha256": old_hashes,
                },
            )
            update = updates / transfer
            update.mkdir(mode=0o700)
            tar_hash = release_tool.source_tar(
                new, life.SOURCE_FILES, update / "source.tar"
            )
            record = {
                "kind": "prism_service_update_v1",
                "base_bundle_sha256": old_bundle,
                "base_run_id": run_id,
                "transfer_tar_sha256": transfer,
                "source_tar_sha256": tar_hash,
                "webapp_sha256": new_hashes[release_tool.PATCH_NAME],
                "file_sha256": new_hashes,
                "release_name": f"service-{transfer}",
            }
            write(update / "manifest.json", record)
            write(
                update / "installed.json",
                {
                    "source_tar_sha256": tar_hash,
                    "transfer_tar_sha256": transfer,
                    "base_bundle_sha256": old_bundle,
                    "base_run_id": run_id,
                },
            )
            with (
                patch.object(life, "BASE", base),
                patch.object(life, "INSTALLED", boot / "installed.json"),
                patch.object(life, "UPDATE_ROOT", updates),
                patch.object(life, "OWNER_UID", os.getuid()),
            ):
                self.assertEqual(
                    life.installation(update / "installed.json", tar_hash, run_id),
                    (str(new), tar_hash, run_id),
                )
                (new / release_tool.PATCH_NAME).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    life.installation(update / "installed.json", tar_hash, run_id)

    def test_v2_update_record_pins_both_changed_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            boot = base / "oneboot"
            boot.mkdir(mode=0o700)
            updates = base / "service-updates"
            updates.mkdir(mode=0o700)
            app = base / "app-releases"
            app.mkdir(mode=0o700)
            old_bundle, run_id, transfer = "a" * 64, "b" * 32, "d" * 64
            old = app / f"{old_bundle}-{run_id}"
            new = app / f"service-{transfer}"
            old.mkdir(mode=0o700)
            new.mkdir(mode=0o700)
            old_hashes, new_hashes = {}, {}
            patches = {
                release_tool.PATCH_NAME: b"fixed webapp",
                release_tool.APP_JS_NAME: b"fixed app js",
            }
            for name in life.SOURCE_FILES:
                original = name.encode()
                updated = patches.get(name, original)
                for directory, content in ((old, original), (new, updated)):
                    path = directory / name
                    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    path.write_bytes(content)
                    path.chmod(0o600)
                old_hashes[name] = hashlib.sha256(original).hexdigest()
                new_hashes[name] = hashlib.sha256(updated).hexdigest()

            def write(path, value):
                path.write_text(json.dumps(value))
                path.chmod(0o600)

            write(
                boot / "installed.json", {"bundle_sha256": old_bundle, "run_id": run_id}
            )
            write(
                boot / "ledger.json",
                {"run_id": run_id, "next_seq": 6, "complete": True},
            )
            write(
                boot / "manifest.json",
                {
                    "kind": "prism_oneboot_app_v1",
                    "run_id": run_id,
                    "bundle_sha256": old_bundle,
                    "raw_sha256": old_bundle,
                    "file_sha256": old_hashes,
                },
            )
            update = updates / transfer
            update.mkdir(mode=0o700)
            tar_hash = release_tool.source_tar(
                new, life.SOURCE_FILES, update / "source.tar"
            )
            manifest = {
                "kind": "prism_service_update_v2",
                "base_bundle_sha256": old_bundle,
                "base_run_id": run_id,
                "transfer_tar_sha256": transfer,
                "source_tar_sha256": tar_hash,
                "webapp_sha256": new_hashes[release_tool.PATCH_NAME],
                "app_js_sha256": new_hashes[release_tool.APP_JS_NAME],
                "file_sha256": new_hashes,
                "release_name": f"service-{transfer}",
            }
            write(update / "manifest.json", manifest)
            write(
                update / "installed.json",
                {
                    "source_tar_sha256": tar_hash,
                    "transfer_tar_sha256": transfer,
                    "base_bundle_sha256": old_bundle,
                    "base_run_id": run_id,
                },
            )
            with (
                patch.object(life, "BASE", base),
                patch.object(life, "INSTALLED", boot / "installed.json"),
                patch.object(life, "UPDATE_ROOT", updates),
                patch.object(life, "OWNER_UID", os.getuid()),
            ):
                self.assertEqual(
                    life.installation(update / "installed.json", tar_hash, run_id),
                    (str(new), tar_hash, run_id),
                )
                manifest.pop("app_js_sha256")
                write(update / "manifest.json", manifest)
                with self.assertRaisesRegex(ValueError, "manifest differs"):
                    life.installation(update / "installed.json", tar_hash, run_id)
                manifest["app_js_sha256"] = new_hashes[release_tool.APP_JS_NAME]
                write(update / "manifest.json", manifest)
                (new / release_tool.APP_JS_NAME).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    life.installation(update / "installed.json", tar_hash, run_id)

    def test_v3_update_record_pins_three_changed_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            boot = base / "oneboot"
            boot.mkdir(mode=0o700)
            updates = base / "service-updates"
            updates.mkdir(mode=0o700)
            app = base / "app-releases"
            app.mkdir(mode=0o700)
            old_bundle, run_id, transfer = "a" * 64, "b" * 32, "e" * 64
            old = app / f"{old_bundle}-{run_id}"
            new = app / f"service-{transfer}"
            old.mkdir(mode=0o700)
            new.mkdir(mode=0o700)
            patches = {
                release_tool.PATCH_NAME: b"fixed webapp",
                release_tool.APP_JS_NAME: b"fixed app js",
                release_tool.IDENTITY_NAME: b"fixed identity",
            }
            old_hashes, new_hashes = {}, {}
            for name in life.SOURCE_FILES:
                original = name.encode()
                updated = patches.get(name, original)
                for directory, content in ((old, original), (new, updated)):
                    path = directory / name
                    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    path.write_bytes(content)
                    path.chmod(0o600)
                old_hashes[name] = hashlib.sha256(original).hexdigest()
                new_hashes[name] = hashlib.sha256(updated).hexdigest()

            def write(path, value):
                path.write_text(json.dumps(value))
                path.chmod(0o600)

            write(
                boot / "installed.json", {"bundle_sha256": old_bundle, "run_id": run_id}
            )
            write(
                boot / "ledger.json",
                {"run_id": run_id, "next_seq": 6, "complete": True},
            )
            write(
                boot / "manifest.json",
                {
                    "kind": "prism_oneboot_app_v1",
                    "run_id": run_id,
                    "bundle_sha256": old_bundle,
                    "raw_sha256": old_bundle,
                    "file_sha256": old_hashes,
                },
            )
            update = updates / transfer
            update.mkdir(mode=0o700)
            tar_hash = release_tool.source_tar(
                new, life.SOURCE_FILES, update / "source.tar"
            )
            manifest = {
                "kind": "prism_service_update_v3",
                "base_bundle_sha256": old_bundle,
                "base_run_id": run_id,
                "transfer_tar_sha256": transfer,
                "source_tar_sha256": tar_hash,
                "webapp_sha256": new_hashes[release_tool.PATCH_NAME],
                "app_js_sha256": new_hashes[release_tool.APP_JS_NAME],
                "identity_sha256": new_hashes[release_tool.IDENTITY_NAME],
                "file_sha256": new_hashes,
                "release_name": f"service-{transfer}",
            }
            write(update / "manifest.json", manifest)
            write(
                update / "installed.json",
                {
                    "source_tar_sha256": tar_hash,
                    "transfer_tar_sha256": transfer,
                    "base_bundle_sha256": old_bundle,
                    "base_run_id": run_id,
                },
            )
            with (
                patch.object(life, "BASE", base),
                patch.object(life, "INSTALLED", boot / "installed.json"),
                patch.object(life, "UPDATE_ROOT", updates),
                patch.object(life, "OWNER_UID", os.getuid()),
            ):
                self.assertEqual(
                    life.installation(update / "installed.json", tar_hash, run_id),
                    (str(new), tar_hash, run_id),
                )
                for field in ("identity_sha256", "app_js_sha256", "webapp_sha256"):
                    changed = dict(manifest)
                    changed.pop(field)
                    write(update / "manifest.json", changed)
                    with (
                        self.subTest(field=field),
                        self.assertRaisesRegex(ValueError, "manifest differs"),
                    ):
                        life.installation(update / "installed.json", tar_hash, run_id)
                write(update / "manifest.json", manifest)
                (new / release_tool.IDENTITY_NAME).write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    life.installation(update / "installed.json", tar_hash, run_id)


if __name__ == "__main__":
    unittest.main()
