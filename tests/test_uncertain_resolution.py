"""Offline checks for the one-run uncertain resolution helper."""

import fcntl
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/gcp/identity-uncertain-resolution.py"
SPEC = importlib.util.spec_from_file_location("uncertain_resolution", SCRIPT)
resolution = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolution)
REAL_TAILNET_CLOSED = resolution.tailnet_closed
REAL_NAMESPACE_EMPTY = resolution.namespace_empty
REAL_INSTALLED_UNITS_PINNED = resolution.installed_units_pinned
REAL_UNIT_PIN = resolution.unit_pin


class UncertainResolutionTests(unittest.TestCase):
    def connection(self):
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        return db

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "demo.sqlite"
        self.run = "a" * 32
        self.resource = "prism-m23-run-" + self.run
        self.token = "b" * 32
        with self.connection() as db:
            db.execute(
                "CREATE TABLE runs (id TEXT PRIMARY KEY,status TEXT,runtime_profile TEXT,"
                "runtime_resource TEXT,runtime_token TEXT,finished REAL,result TEXT,error TEXT)"
            )
            db.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY,at REAL,kind TEXT,"
                "actor TEXT,resource TEXT,outcome TEXT)"
            )
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                (self.run, "uncertain", "reference-linux", self.resource,
                 self.token, 123.0, None, "Previous watchdog evidence"),
            )
        self.path.chmod(0o600)
        self.lock_path = self.path.with_name("server.lock")
        self.lock_path.touch(mode=0o600)
        self.lock_path.chmod(0o600)
        patches = [
            patch.object(resolution, "DB", self.path),
            patch.object(resolution, "OWNER_UID", os.getuid()),
            patch.object(resolution.os, "geteuid", return_value=0),
            patch.object(resolution, "service_stopped"),
            patch.object(resolution, "installed_units_pinned"),
            patch.object(resolution, "tailnet_closed"),
            patch.object(resolution, "watchdog_healthy"),
            patch.object(resolution, "namespace_empty"),
            patch.object(resolution, "no_matching_process"),
        ]
        self.mocks = [item.start() for item in patches]
        for item in patches:
            self.addCleanup(item.stop)

    def state(self):
        with self.connection() as db:
            row = db.execute(
                "SELECT status,finished,result,error FROM runs WHERE id=?", (self.run,)
            ).fetchone()
            events = db.execute(
                "SELECT kind,actor,resource,outcome FROM events"
            ).fetchall()
        return row, events

    def test_inspect_is_read_only_and_resolution_preserves_prior_evidence(self):
        report = resolution.execute("inspect", self.run)
        self.assertEqual(report["changed"], False)
        self.assertEqual(self.state(),
                         (("uncertain", 123.0, None, "Previous watchdog evidence"), []))
        report = resolution.execute("resolve", self.run)
        self.assertEqual(report["execution_outcome"], "unknown")
        row, events = self.state()
        self.assertEqual(row, ("failed", 123.0, None, resolution.ERROR))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:3],
                         ("run_uncertain_resolved", "local-root-operator", self.run))
        self.assertIn('"original_error":"Previous watchdog evidence"', events[0][3])
        with self.assertRaises(resolution.ResolutionDenied):
            resolution.execute("resolve", self.run)
        self.assertEqual(len(self.state()[1]), 1)

    def test_host_check_denials_leave_row_unchanged(self):
        for check in self.mocks[3:]:
            with self.subTest(check=check):
                check.side_effect = resolution.ResolutionDenied("unsafe host")
                with self.assertRaises(resolution.ResolutionDenied):
                    resolution.execute("resolve", self.run)
                self.assertEqual(self.state()[0][0], "uncertain")
                self.assertEqual(self.state()[1], [])
                check.side_effect = None

    def test_active_work_and_identity_ambiguity_are_denied(self):
        with self.connection() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                       ("c" * 32, "queued", "reference-linux", "other", "d" * 32,
                        None, None, None))
        with self.assertRaisesRegex(resolution.ResolutionDenied, "Queued"):
            resolution.execute("resolve", self.run)
        with self.connection() as db:
            db.execute("UPDATE runs SET status='failed',runtime_token=? WHERE id=?",
                       (self.token, "c" * 32))
        with self.assertRaisesRegex(resolution.ResolutionDenied, "shared"):
            resolution.execute("resolve", self.run)
        self.assertEqual(self.state()[0][0], "uncertain")

    def test_non_uncertain_or_non_null_result_is_denied(self):
        for field, value in (("status", "completed"), ("result", "untrusted"),
                             ("runtime_resource", "wrong")):
            with self.subTest(field=field):
                with self.connection() as db:
                    db.execute(f"UPDATE runs SET {field}=? WHERE id=?", (value, self.run))
                with self.assertRaises(resolution.ResolutionDenied):
                    resolution.execute("resolve", self.run)
                with self.connection() as db:
                    db.execute(f"UPDATE runs SET {field}=? WHERE id=?",
                               ({"status": "uncertain", "result": None,
                                 "runtime_resource": self.resource}[field], self.run))
        self.assertEqual(self.state()[1], [])

    def test_database_symlink_and_world_readable_file_are_denied(self):
        link = self.path.with_name("linked.sqlite")
        link.symlink_to(self.path)
        with (patch.object(resolution, "DB", link),
              self.assertRaisesRegex(resolution.ResolutionDenied, "private")):
            resolution.execute("inspect", self.run)
        self.path.chmod(0o644)
        with self.assertRaisesRegex(resolution.ResolutionDenied, "private"):
            resolution.execute("inspect", self.run)

    def test_audit_failure_rolls_back_run_update(self):
        with self.connection() as db:
            db.execute("CREATE TRIGGER deny_audit BEFORE INSERT ON events "
                       "BEGIN SELECT RAISE(ABORT, 'audit denied'); END")
        with self.assertRaises(sqlite3.Error):
            resolution.execute("resolve", self.run)
        self.assertEqual(self.state(),
                         (("uncertain", 123.0, None, "Previous watchdog evidence"), []))

    def test_tailnet_requires_shields_up_and_private_host_tag(self):
        status = {"BackendState": "Running", "Self": {
            "Online": True, "Tags": ["tag:prism-host"]}}
        prefs = {"ShieldsUp": True, "RunSSH": False, "RouteAll": False,
                 "AdvertiseRoutes": [], "ExitNodeID": "", "ExitNodeIP": ""}
        with patch.object(resolution, "command", side_effect=[
            json.dumps(status).encode(), json.dumps(prefs).encode()]):
            REAL_TAILNET_CLOSED()
        prefs["ShieldsUp"] = False
        with (patch.object(resolution, "command", side_effect=[
                json.dumps(status).encode(), json.dumps(prefs).encode()]),
              self.assertRaises(resolution.ResolutionDenied)):
            REAL_TAILNET_CLOSED()

    def test_namespace_requires_exact_empty_listing(self):
        with patch.object(resolution, "command", return_value=b"\n") as command:
            REAL_NAMESPACE_EMPTY()
            self.assertEqual(command.call_args.args[0], [
                resolution.NERDCTL, "--address", resolution.CONTAINERD_SOCKET,
                "--namespace", "prism-m0", "ps", "-a", "--format", "{{.ID}}"])
        with (patch.object(resolution, "command", return_value=b"other-resource\n"),
              self.assertRaises(resolution.ResolutionDenied)):
            REAL_NAMESPACE_EMPTY()

    def test_occupied_service_lock_denies_before_database_read(self):
        with self.lock_path.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(resolution.ResolutionDenied, "occupied"):
                resolution.execute("resolve", self.run)
        self.assertEqual(self.state()[0][0], "uncertain")

    def test_service_lock_symlink_is_denied(self):
        self.lock_path.unlink()
        self.lock_path.symlink_to(self.path)
        with self.assertRaises(resolution.ResolutionDenied):
            resolution.execute("resolve", self.run)
        self.assertEqual(self.state()[0][0], "uncertain")

    def test_installed_units_require_v7_release_and_watchdog_binding(self):
        pinned = (f"--hostname private.example.ts.net --bind-host 100.100.100.100 "
                  f"--installed-record {resolution.UPDATE} "
                  f"--bundle-sha256 {resolution.SOURCE} --run-id {'a' * 32}")
        service = [
            "User=root", "Restart=no",
            "BindsTo=tailscaled.service " + resolution.WATCHDOG,
            "Requires=tailscaled.service prism-identify-boot-restore.service "
            + resolution.WATCHDOG,
            f"ExecStartPre=/usr/bin/python3 {resolution.LIFECYCLE} prepare {pinned}",
            f"ExecStartPre=/usr/bin/python3 {resolution.LIFECYCLE} watchdog-ready {pinned}",
            f"ExecStartPre=/usr/bin/python3 {resolution.GUEST} preflight fixed",
            f"ExecStart=/usr/bin/python3 {resolution.LIFECYCLE} serve {pinned}",
            f"ExecStartPost=/usr/bin/python3 {resolution.LIFECYCLE} open {pinned}",
            (f"ExecStopPost=/usr/bin/python3 {resolution.LIFECYCLE} close "
             "--hostname private.example.ts.net --bind-host 100.100.100.100"),
        ]
        watch_pin = pinned + f" --release {resolution.RELEASE} --db {self.path}"
        watcher = [
            "User=root", "Restart=on-failure",
            "RuntimeDirectory=prism-host-watchdog", "RuntimeDirectoryMode=0700",
            f"ExecStartPre=/usr/bin/python3 {resolution.LIFECYCLE} watchdog-prepare {watch_pin}",
            f"ExecStart=/usr/bin/python3 {resolution.LIFECYCLE} watchdog-serve {watch_pin}",
            (f"ExecStopPost=/usr/bin/python3 {resolution.LIFECYCLE} close "
             "--hostname private.example.ts.net --bind-host 100.100.100.100"),
        ]
        def unit_lines(unit):
            return service if unit == resolution.SERVICE else watcher
        with (patch.object(resolution, "unit_pin", side_effect=unit_lines),
              patch.object(resolution, "systemd", return_value=
                           "tailscaled.service " + resolution.WATCHDOG)):
            REAL_INSTALLED_UNITS_PINNED()
            watcher[5] = watcher[5].replace(resolution.RELEASE, "/wrong/release")
            with self.assertRaises(resolution.ResolutionDenied):
                REAL_INSTALLED_UNITS_PINNED()
            watcher[5] = watcher[5].replace("/wrong/release", resolution.RELEASE)
            service[2] = "BindsTo=tailscaled.service"
            with self.assertRaises(resolution.ResolutionDenied):
                REAL_INSTALLED_UNITS_PINNED()
            service[2] = "BindsTo=tailscaled.service " + resolution.WATCHDOG
            service.pop()
            with self.assertRaises(resolution.ResolutionDenied):
                REAL_INSTALLED_UNITS_PINNED()
            service.append(
                f"ExecStopPost=/usr/bin/python3 {resolution.LIFECYCLE} close "
                "--hostname private.example.ts.net --bind-host 100.100.100.100")
            watcher[-1] = watcher[-1].replace("100.100.100.100", "100.100.100.101")
            with self.assertRaises(resolution.ResolutionDenied):
                REAL_INSTALLED_UNITS_PINNED()
            watcher[-1] = watcher[-1].replace("100.100.100.101", "100.100.100.100")
            watcher.append("ExecCondition=/bin/true")
            with self.assertRaises(resolution.ResolutionDenied):
                REAL_INSTALLED_UNITS_PINNED()
            watcher.pop()
            watcher.append("User=nobody")
            with self.assertRaises(resolution.ResolutionDenied):
                REAL_INSTALLED_UNITS_PINNED()

    def test_unit_pin_rejects_reload_needed_and_symlink(self):
        unit = self.path.with_name("watchdog.service")
        unit.write_text("[Unit]\n")
        unit.chmod(0o600)
        with (patch.object(resolution, "UNIT_ROOT", unit.parent),
              patch.object(resolution, "systemd", side_effect=lambda _unit, field:
                           str(unit) if field == "FragmentPath" else "yes"),
              self.assertRaises(resolution.ResolutionDenied)):
            REAL_UNIT_PIN(unit.name)
        with (patch.object(resolution, "UNIT_ROOT", unit.parent),
              patch.object(resolution, "systemd", side_effect=lambda _unit, field:
                           str(unit) if field == "FragmentPath" else
                           "no" if field == "NeedDaemonReload" else "/etc/systemd/system/override.conf"),
              self.assertRaises(resolution.ResolutionDenied)):
            REAL_UNIT_PIN(unit.name)
        unit.unlink()
        unit.symlink_to(self.path)
        with (patch.object(resolution, "UNIT_ROOT", unit.parent),
              self.assertRaises(resolution.ResolutionDenied)):
            REAL_UNIT_PIN(unit.name)


if __name__ == "__main__":
    unittest.main()
