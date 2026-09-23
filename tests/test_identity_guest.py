"""Offline checks for the private guest identity-service helper."""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/gcp/identity-service-guest.py"
SPEC = importlib.util.spec_from_file_location("identity_service_guest", SCRIPT)
guest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guest)


class GuestIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.client = self.root / "client-id"
        self.client.write_text("registered-client-id\n")
        self.client.chmod(0o600)
        self.subject = self.root / "subject"
        self.subject.write_text("verified-subject\n")
        self.subject.chmod(0o600)

    def args(self, phase, output):
        return SimpleNamespace(
            phase=phase,
            hostname="pilot.example.ts.net",
            client_id_file=str(self.client),
            owner_subject_file=str(self.subject) if phase == "service" else None,
            output=str(output),
        )

    def project(self):
        source = Path(__file__).resolve().parents[1] / "examples/document-handoff"
        project = self.root / "project"
        shutil.copytree(source, project)
        for path in project.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        project.chmod(0o700)
        return project

    def test_fixed_project_inventory_includes_unlisted_canary_and_denies_mutation(self):
        project = self.project()
        with (
            patch.object(guest, "PROJECT_ROOT", project),
            patch.object(guest, "PROJECT_MANIFEST", project / ".prism-project.json"),
            patch.object(guest, "PROJECT_OWNER", os.getuid()),
        ):
            pinned = guest.project_inventory()
            self.assertEqual(guest.project_inventory(pinned), pinned)
            canary = project / "unlisted-canary.txt"
            canary.write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "inventory changed"):
                guest.project_inventory(pinned)
            canary.unlink()
            with self.assertRaisesRegex(ValueError, "inventory changed"):
                guest.project_inventory(pinned)
            canary.write_text("restored\n")
            canary.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private"):
                guest.project_inventory()
            canary.unlink()
            canary.symlink_to(project / "README.md")
            with self.assertRaisesRegex(ValueError, "owner controlled|Unsupported"):
                guest.project_inventory()

    def test_fixed_project_rejects_missing_listed_file_and_wrong_owner(self):
        project = self.project()
        with (
            patch.object(guest, "PROJECT_ROOT", project),
            patch.object(guest, "PROJECT_MANIFEST", project / ".prism-project.json"),
            patch.object(guest, "PROJECT_OWNER", os.getuid()),
        ):
            (project / "config/release.json").unlink()
            with self.assertRaisesRegex(
                ValueError, "listed project file is missing|Empty project directory"
            ):
                guest.project_inventory()
            with (
                patch.object(guest, "PROJECT_OWNER", os.getuid() + 1),
                self.assertRaisesRegex(ValueError, "ownership|private"),
            ):
                guest.project_inventory()

    def test_project_hash_flows_to_preflight_without_expanding_catalog(self):
        project = self.project()
        config = self.root / "service.json"
        guest.render(self.args("service", config))
        release = self.root / "release"
        (release / "venv/bin").mkdir(parents=True)
        python = release / "venv/bin/python"
        python.write_text("placeholder")
        python.chmod(0o700)
        arguments = SimpleNamespace(
            port=8443,
            hostname="pilot.example.ts.net",
            bind_host="100.100.100.100",
            release=str(release),
            oidc_config=str(config),
            client_secret_file="/private/secret",
            tls_cert_file="/private/cert",
            tls_key_file="/private/key",
            model_budget_cents=0,
        )
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"local_inputs_valid": True, "pilot_ready": False}),
            stderr="",
        )
        with (
            patch.object(guest, "PROJECT_ROOT", project),
            patch.object(guest, "PROJECT_MANIFEST", project / ".prism-project.json"),
            patch.object(guest, "PROJECT_OWNER", os.getuid()),
            patch.object(guest.subprocess, "run", return_value=result) as run,
        ):
            arguments.project_sha256 = guest.project_inventory()
            guest.command(arguments, "preflight")
            argv = run.call_args.args[0]
            self.assertEqual(
                argv[argv.index("--project") + 1], str(project / ".prism-project.json")
            )
            self.assertNotIn("unlisted-canary.txt", argv)
            (project / "unlisted-canary.txt").unlink()
            with self.assertRaisesRegex(ValueError, "inventory changed"):
                guest.command(arguments, "preflight")
            self.assertEqual(run.call_count, 1)

    def test_two_phase_config_and_exclusive_output(self):
        identify = self.root / "identify.json"
        service = self.root / "service.json"
        guest.render(self.args("identify", identify))
        guest.render(self.args("service", service))
        first = json.loads(identify.read_text())
        second = json.loads(service.read_text())
        self.assertNotIn("owner_subject", first)
        self.assertEqual(second["owner_subject"], "verified-subject")
        self.assertEqual(
            first["redirect_uri"],
            first["public_origin"] + "/auth/oidc/identify/callback",
        )
        self.assertEqual(
            second["redirect_uri"], second["public_origin"] + "/auth/oidc/callback"
        )
        self.assertEqual(service.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            guest.render(self.args("service", service))

    def test_rejects_insecure_input_and_missing_verified_subject(self):
        self.client.chmod(0o644)
        with self.assertRaises(ValueError):
            guest.render(self.args("identify", self.root / "bad.json"))
        self.client.chmod(0o600)
        args = self.args("service", self.root / "bad.json")
        args.owner_subject_file = None
        with self.assertRaises(ValueError):
            guest.render(args)
        args = self.args("identify", self.root / "bad.json")
        args.hostname = "public.example.com"
        with self.assertRaises(ValueError):
            guest.render(args)

    def test_preflight_has_zero_model_budget_and_clean_environment(self):
        config = self.root / "service.json"
        guest.render(self.args("service", config))
        release = self.root / "release"
        (release / "venv/bin").mkdir(parents=True)
        python = release / "venv/bin/python"
        python.write_text("placeholder")
        python.chmod(0o700)
        arguments = SimpleNamespace(
            port=8443,
            hostname="pilot.example.ts.net",
            bind_host="100.100.100.100",
            release=str(release),
            oidc_config=str(config),
            client_secret_file="/private/secret",
            tls_cert_file="/private/cert",
            tls_key_file="/private/key",
            data_dir=str(self.root / "identity-state"),
            model_budget_cents=0,
        )
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"local_inputs_valid": True, "pilot_ready": False}),
            stderr="",
        )
        with patch.object(guest.subprocess, "run", return_value=result) as run:
            guest.command(arguments, "preflight")
        argv = run.call_args.args[0]
        self.assertNotIn("--allow-openai", argv)
        self.assertNotIn("--probe-idp-tls", argv)
        self.assertEqual(
            run.call_args.kwargs["env"]["PYTHONPATH"], str(release / "src")
        )
        self.assertNotIn("HOME", run.call_args.kwargs["env"])
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", run.call_args.kwargs["env"])

    def test_model_route_requires_private_fixed_key_and_approved_budget(self):
        args = SimpleNamespace(model_budget_cents=100)
        key = self.root / "openai-model-key"
        key.write_text("sk-" + "x" * 32)
        key.chmod(0o600)
        with patch.object(guest, "MODEL_KEY", str(key)):
            self.assertEqual(
                guest.model_settings(args),
                ["--allow-openai", "--openai-key-file", str(key)],
            )
            args.model_budget_cents = 1000
            self.assertEqual(
                guest.model_settings(args),
                ["--allow-openai", "--openai-key-file", str(key)],
            )
            key.chmod(0o644)
            with self.assertRaises(ValueError):
                guest.model_settings(args)
            key.chmod(0o600)
            for invalid in (-1, 1, 101, 1001, True):
                args.model_budget_cents = invalid
                with self.assertRaises(ValueError):
                    guest.model_settings(args)
            args.model_budget_cents = 0
            self.assertEqual(guest.model_settings(args), [])

    def test_serve_passes_only_key_path_and_budget(self):
        config = self.root / "service.json"
        guest.render(self.args("service", config))
        release = self.root / "release"
        (release / "venv/bin").mkdir(parents=True)
        python = release / "venv/bin/python"
        python.write_text("placeholder")
        python.chmod(0o700)
        key = self.root / "openai-model-key"
        secret = "sk-" + "x" * 32
        key.write_text(secret)
        key.chmod(0o600)
        args = SimpleNamespace(
            port=8443,
            hostname="pilot.example.ts.net",
            bind_host="100.100.100.100",
            release=str(release),
            oidc_config=str(config),
            client_secret_file="/private/secret",
            tls_cert_file="/private/cert",
            tls_key_file="/private/key",
            data_dir=str(self.root / "identity-state"),
            model_budget_cents=1000,
        )
        with (
            patch.object(guest, "MODEL_KEY", str(key)),
            patch.object(guest.os, "dup2"),
            patch.object(guest.os, "execve") as launch,
        ):
            guest.command(args, "serve")
        argv = launch.call_args.args[1]
        self.assertIn("--allow-openai", argv)
        self.assertEqual(argv[argv.index("--openai-key-file") + 1], str(key))
        self.assertEqual(argv[argv.index("--model-budget-cents") + 1], "1000")
        self.assertNotIn(secret, argv)
        self.assertNotIn(secret, launch.call_args.args[2].values())


if __name__ == "__main__":
    unittest.main()
