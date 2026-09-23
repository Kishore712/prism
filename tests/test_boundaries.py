"""Fast regression checks; fake engine cases do not establish real isolation."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prism.cli import main
from prism.doctor import diagnose
from prism.engine import Engine, EngineError, IMAGE, socket_path
from prism.fixtures.probe import evaluate
from prism.runtime import container_spec, DevelopmentRuntime, LABEL, LogReader, OUTPUT_LIMIT, probe_arguments
from prism.selftest import synthetic_environment


class ObservedEngine:
    def version(self):
        return {"Version": "test", "Arch": "arm64", "ApiVersion": "1.47"}

    def info(self):
        return {"cgroup_version": "2"}

    def image(self):
        return "sha256:test"


class DiagnosticTests(unittest.TestCase):
    def test_prerequisites_do_not_claim_isolation(self):
        report = diagnose(ObservedEngine())
        self.assertTrue(report["prerequisites_ready"])
        self.assertFalse(report["pilot_ready"])
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "isolation")["status"], "unverified")

    def test_missing_image_blocks_prerequisites(self):
        engine = ObservedEngine()
        engine.image = lambda: (_ for _ in ()).throw(EngineError("missing", 404))
        self.assertFalse(diagnose(engine)["prerequisites_ready"])

    def test_pilot_does_not_fall_back_to_docker(self):
        engine = ObservedEngine()
        engine.version = lambda: self.fail("Pilot diagnostic called the development engine")
        report = diagnose(engine, "pilot")
        self.assertFalse(report["prerequisites_ready"])
        self.assertFalse(report["pilot_ready"])

    def test_invalid_remote_endpoints_are_rejected(self):
        for endpoint in ["tcp://localhost:2375", "ssh://host", "relative.sock", "bad\0path"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                socket_path(endpoint)

    def test_missing_socket_has_safe_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(EngineError) as caught:
                Engine(Path(folder) / "missing.sock", timeout=0.1).version()
            self.assertNotIn(folder, str(caught.exception))

    def test_malformed_version_fails_closed(self):
        engine = Engine("/unused.sock")
        for data in [None, {}, {"Version": "", "MinAPIVersion": "1.24", "ApiVersion": "1.47", "Os": "linux"}, {"MinAPIVersion": "x", "ApiVersion": "1.47"}]:
            with self.subTest(data=data), patch.object(engine, "request", return_value=data), self.assertRaises(EngineError):
                engine.version()

    def test_newer_minimum_api_is_not_silently_used(self):
        engine = Engine("/unused.sock")
        with patch.object(engine, "request", return_value={"Version": "x", "MinAPIVersion": "1.48", "ApiVersion": "1.50", "Os": "linux"}), self.assertRaises(EngineError):
            engine.version()

    def test_wrong_image_digest_is_rejected(self):
        engine = Engine("/unused.sock")
        with patch.object(engine, "request", return_value={"Id": "sha256:test", "RepoDigests": ["python@sha256:wrong"]}), self.assertRaises(EngineError):
            engine.image()

    def test_implicit_image_volumes_are_rejected(self):
        engine = Engine("/unused.sock")
        with patch.object(engine, "request", return_value={"Id": "sha256:test", "RepoDigests": [IMAGE], "Config": {"Volumes": {"/data": {}}}}), self.assertRaises(EngineError):
            engine.image()

    def test_cgroup_v1_is_not_accepted(self):
        engine = Engine("/unused.sock")
        with patch.object(engine, "request", return_value={"CgroupVersion": "1"}), self.assertRaises(EngineError):
            engine.info()

    def test_no_seccomp_is_not_accepted(self):
        engine = Engine("/unused.sock")
        with patch.object(engine, "request", return_value={"CgroupVersion": "2", "SecurityOptions": []}), self.assertRaises(EngineError):
            engine.info()

    def test_doctor_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as folder:
            diagnose(Engine(Path(folder) / "missing.sock"))
            self.assertEqual(list(Path(folder).iterdir()), [])


class ToolBoundaryTests(unittest.TestCase):
    def test_real_environment_canary_is_present_then_restored(self):
        with patch.dict(os.environ, {"PRISM_PRIVATE_CANARY": "original-synthetic-value"}):
            with synthetic_environment():
                self.assertEqual(os.environ["PRISM_PRIVATE_CANARY"], "PRISM_SYNTHETIC_ENVIRONMENT_CANARY")
            self.assertEqual(os.environ["PRISM_PRIVATE_CANARY"], "original-synthetic-value")

    def test_environment_canary_is_removed_after_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError), synthetic_environment():
                raise RuntimeError("synthetic failure")
            self.assertNotIn("PRISM_PRIVATE_CANARY", os.environ)

    def test_seed_rejects_strings_booleans_floats_and_out_of_range(self):
        for value in [True, "7", "7; touch /tmp/x", "--help", 1.5, -1, 1_000_001, None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe_arguments("evaluate", value)

    def test_only_fixed_programs_can_be_selected(self):
        for action in ["sh", "python", "evaluate;id", "/bin/bash"]:
            with self.assertRaises(ValueError):
                probe_arguments(action)

    def test_no_arbitrary_extra_arguments(self):
        with self.assertRaises(ValueError):
            probe_arguments("sleep", "--privileged")

    def test_environment_and_host_mounts_are_not_inherited(self):
        with patch.dict(os.environ, {"PRISM_PRIVATE_CANARY": "synthetic-only", "DOCKER_HOST": "tcp://evil:2375"}):
            spec = container_spec("sha256:test", "evaluate", 7, "test-token")
        self.assertNotIn("synthetic-only", json.dumps(spec))
        self.assertNotIn("Binds", spec["HostConfig"])
        self.assertNotIn("Mounts", spec["HostConfig"])
        self.assertEqual(spec["HostConfig"]["NetworkMode"], "none")
        self.assertTrue(spec["HostConfig"]["ReadonlyRootfs"])
        self.assertFalse(spec["HostConfig"]["Privileged"])
        self.assertEqual(spec["User"], "65534:65534")
        self.assertIn("seccomp=builtin", spec["HostConfig"]["SecurityOpt"])

    def test_real_fixture_reproducible(self):
        self.assertEqual(evaluate(7), evaluate(7))
        self.assertNotEqual(evaluate(7), evaluate(19))
        self.assertTrue(evaluate(7)["synthetic"])

    def test_cleanup_refuses_another_resource(self):
        runtime = object.__new__(DevelopmentRuntime)
        runtime.owned = {}
        with self.assertRaises(ValueError):
            runtime._cleanup("unrelated-container")

    def test_cleanup_checks_ownership_label(self):
        runtime = object.__new__(DevelopmentRuntime)
        runtime.owned = {"prism-m0-test": "our-token"}
        runtime.engine = Engine("/unused.sock")
        with patch.object(runtime.engine, "request", return_value={"Config": {"Labels": {LABEL: "other-token"}}}) as request:
            with self.assertRaises(EngineError):
                runtime._cleanup("prism-m0-test")
            self.assertEqual(request.call_count, 1)


class LogTests(unittest.TestCase):
    def reader(self, data):
        class Fake:
            @contextlib.contextmanager
            def response(self, *args, **kwargs):
                yield io.BytesIO(data)
        return LogReader(Fake(), "test", 1)

    def test_multiplexed_stdout_and_stderr(self):
        reader = self.reader(b"\x01\0\0\0\0\0\0\x02ok\x02\0\0\0\0\0\0\x03err")
        reader.run()
        self.assertEqual(reader.stdout, b"ok")
        self.assertEqual(reader.stderr, b"err")
        self.assertFalse(reader.failed)

    def test_oversized_frame_is_not_allocated(self):
        reader = self.reader(b"\x01\0\0\0" + (OUTPUT_LIMIT + 1).to_bytes(4, "big"))
        reader.run()
        self.assertTrue(reader.exceeded.is_set())
        self.assertEqual(len(reader.stdout), 0)

    def test_incomplete_frame_fails(self):
        reader = self.reader(b"\x01\0\0\0\0\0\0\x04ab")
        reader.run()
        self.assertTrue(reader.failed)

    def test_unknown_channel_fails(self):
        reader = self.reader(b"\x03\0\0\0\0\0\0\0")
        reader.run()
        self.assertTrue(reader.failed)


class CliTests(unittest.TestCase):
    def test_pilot_selftest_cannot_start_development_runtime(self):
        with patch("prism.cli.run_selftest") as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["selftest", "--profile", "pilot", "--json"]), 2)
        run.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["pilot_ready"])

    def test_unavailable_socket_is_nonzero_and_json(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["doctor", "--socket", str(Path(folder) / "absent"), "--json"]), 2)
        self.assertFalse(json.loads(output.getvalue())["prerequisites_ready"])


if __name__ == "__main__":
    unittest.main()
