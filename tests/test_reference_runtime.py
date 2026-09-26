"""M2.3 reference-profile policy tests; no local runtime or cloud is used."""

import hashlib
import importlib.util
import io
import json
import os
import platform
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from prism import worker as worker_module
from prism.conversation import Conversations
from prism.engine import IMAGE, EngineError
from prism.handoff import Handoffs, HandoffSelection
from prism.host_watchdog import WatchdogError
from prism.jobs import SOURCE_ROOT, WORKER_BOOTSTRAP, Jobs
from prism.owner import OwnerIdentity, OwnerWorkspace
from prism.projects import ProjectSource
from prism.reference_runtime import (
    ENV,
    HANDLER,
    KATA_CHECK_ENV,
    KATA_POLICY,
    LABEL,
    ReferenceLinuxRuntime,
    RuntimeRegistry,
    _command,
    _fixed_command,
    _listed_image_id,
    _validate_kata_config,
    _validated_image_metadata,
    _verified_image_id,
    reference_argv,
)
from prism.runtime import RunResult, action_program
from prism.sharing import Denied, NamedPrincipal, Store, ident, packed
from prism.worker import _start_lease_monitor

check_spec = importlib.util.spec_from_file_location(
    "m2_linux_runtime_check",
    Path(__file__).resolve().parents[1] / "scripts/m2-linux-runtime-check.py",
)
m2_check = importlib.util.module_from_spec(check_spec)
check_spec.loader.exec_module(m2_check)


def project(root):
    root.mkdir()
    (root / "data.json").write_text('{"ok":true}\n')
    manifest = root / ".prism-project.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "id": "reference-docs",
                "title": "Reference documents",
                "files": ["data.json"],
                "action": "json-check",
            }
        )
    )
    return ProjectSource.from_manifest(manifest, action_profile="reference-linux")


class FakeReference:
    image_id = "sha256:reference"

    def __init__(self, *, leftovers=(), fail_cleanup=False):
        self.readiness = {"ready": True, "profile": "reference-linux"}
        self.leftovers = list(leftovers)
        self.fail_cleanup = fail_cleanup
        self.reconciled = []

    @staticmethod
    def resource_name(run):
        return "prism-m23-run-" + run

    def reconcile(self, resource, token, *, settle_seconds=0):
        self.reconciled.append((resource, token, settle_seconds))
        if self.fail_cleanup:
            raise EngineError("synthetic cleanup failure")
        return True

    def all_resources(self):
        return self.leftovers


class FakeRegistry(RuntimeRegistry):
    def __init__(self, reference):
        super().__init__(profile="reference-linux", reference=reference)

    def assert_ready(self, profile):
        if profile != self.profile or self.blocked:
            raise EngineError("not ready")

    def activate(self):
        self.activated = True


class FakeWatchdog:
    def __init__(self):
        self.calls = []

    def health(self):
        self.calls.append("health")

    def register(self, *args):
        self.calls.append("register")

    def attach(self, *args):
        self.calls.append("attach")

    def renew(self, *args):
        self.calls.append("renew")

    def finishing(self, *args):
        self.calls.append("finishing")

    def abort(self, *args):
        self.calls.append("abort")

    def release(self, *args):
        self.calls.append("release")


class ReferencePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = project(self.root / "project")
        self.store = Store(self.root / "state.sqlite")
        manifest = self.source.freeze(
            ["data.json"], "Check the selected JSON document.", "verify"
        )
        candidate = self.store.candidate(manifest)
        self.store.approve(candidate["id"], candidate["digest"])
        self.session = self.store.new_session(candidate["id"], "reviewer")

    def jobs(self, reference=None, *, recover=True):
        return Jobs(
            self.store,
            recover=recover,
            registry=FakeRegistry(reference or FakeReference()),
            watchdog=FakeWatchdog(),
        )

    def test_dispatch_observation_requires_exact_owned_running_resource(self):
        jobs = self.jobs(recover=False)
        self.addCleanup(jobs.shutdown)
        run_id = "c" * 32
        resource = "prism-m23-run-" + run_id
        token = "d" * 32
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO runs(id,session,request_key,seed,status,created,action,"
                "parameters,runtime_profile,runtime_resource,runtime_token) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    self.session["id"],
                    "dispatch-observation",
                    0,
                    "running",
                    0,
                    "json-check",
                    "{}",
                    "reference-linux",
                    resource,
                    token,
                ),
            )

        class ObservedRuntime:
            def __init__(self):
                self.states = ["created", "running"]
                self.identities = []

            def inspect_owned(self, name, observed_token):
                self.identities.append((name, observed_token))
                return {"State": {"Status": self.states.pop(0)}}

        runtime = ObservedRuntime()
        observed = m2_check.wait_dispatch_observation(
            self.store, runtime, run_id, timeout=0.1, poll=0
        )
        self.assertTrue(observed["owned_running"])
        self.assertEqual(observed["resource"], resource)
        self.assertEqual(observed["token"], token)
        self.assertEqual(runtime.identities, [(resource, token), (resource, token)])

    def test_dispatch_observation_does_not_upgrade_completed_run(self):
        jobs = self.jobs(recover=False)
        self.addCleanup(jobs.shutdown)
        run_id = "e" * 32
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO runs(id,session,request_key,seed,status,created,action,"
                "parameters,runtime_profile,runtime_resource,runtime_token) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    self.session["id"],
                    "dispatch-completed",
                    0,
                    "completed",
                    0,
                    "json-check",
                    "{}",
                    "reference-linux",
                    "prism-m23-run-" + run_id,
                    "f" * 32,
                ),
            )
        runtime = SimpleNamespace(
            inspect_owned=lambda *_: self.fail("completed run must not be upgraded")
        )
        observed = m2_check.wait_dispatch_observation(
            self.store, runtime, run_id, timeout=0.1, poll=0
        )
        self.assertEqual(observed["status"], "completed")
        self.assertFalse(observed["owned_running"])

    def test_active_revoke_requires_trusted_cancel_and_exact_cleanup(self):
        trusted = {
            "status": "cancelled",
            "result": None,
            "error": "Execution was cancelled and its container was removed.",
        }
        cases = (
            ("trusted_cancel", True, trusted, True, True),
            (
                "completed_after_observation",
                True,
                {"status": "completed", "result": "{}", "error": None},
                True,
                False,
            ),
            (
                "uncertain",
                True,
                {"status": "uncertain", "result": None, "error": "worker lost"},
                True,
                False,
            ),
            (
                "wrong_error",
                True,
                dict(trusted, error="different error"),
                True,
                False,
            ),
            (
                "non_null_result",
                True,
                dict(trusted, result="{}"),
                True,
                False,
            ),
            ("resource_remains", True, trusted, False, False),
            ("not_observed_active", False, trusted, True, False),
        )
        for name, active, record, resource_absent, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    m2_check.cancelled_after_active_revoke(
                        active, record, resource_absent
                    ),
                    expected,
                )

    def test_reference_action_is_operator_bound_and_launch_is_durable(self):
        action = self.session["manifest"]["action"]
        self.assertEqual(action["profile"], "reference-linux")
        self.assertEqual(action["runtime_handler"], HANDLER)
        self.assertEqual(action["profile_revision"], 1)
        jobs = self.jobs(recover=False)
        with patch.object(jobs, "_launch") as launch:
            run = jobs.submit_json_check(
                self.session["id"], "reviewer", "reference-request"
            )
            jobs.registry.blocked = True
            self.assertEqual(
                jobs.submit_json_check(
                    self.session["id"], "reviewer", "reference-request"
                )["id"],
                run["id"],
            )
            with self.store.connect() as db:
                db.execute(
                    "UPDATE runs SET status='completed' WHERE id=?", (run["id"],)
                )
            self.assertEqual(
                jobs.submit_json_check(
                    self.session["id"], "reviewer", "reference-request"
                )["id"],
                run["id"],
            )
        args = launch.call_args.args
        self.assertEqual(args[7], "reference-linux")
        self.assertEqual(args[8], "prism-m23-run-" + run["id"])
        self.assertEqual(len(args[9]), 32)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run["id"],)).fetchone()
        self.assertEqual(row["runtime_profile"], "reference-linux")
        self.assertEqual(row["runtime_resource"], args[8])
        self.assertEqual(row["runtime_token"], args[9])
        self.assertNotIn(args[9], repr(run))

    def test_profile_policy_forgery_is_rejected_before_launch(self):
        manifest = self.source.freeze(
            ["data.json"], "Check a forged runtime policy.", "verify"
        )
        manifest["action"]["runtime_handler"] = "io.containerd.runc.v2"
        candidate = self.store.candidate(manifest)
        self.store.approve(candidate["id"], candidate["digest"])
        session = self.store.new_session(candidate["id"], "reviewer")
        jobs = self.jobs(recover=False)
        with (
            patch.object(jobs, "_launch") as launch,
            self.assertRaises(Denied) as caught,
        ):
            jobs.submit_json_check(session["id"], "reviewer", "forged-reference")
        self.assertEqual(caught.exception.status, 409)
        launch.assert_not_called()

    def test_restart_cleans_observed_resource_but_remains_uncertain_without_retry(self):
        first = self.jobs(recover=False)
        with patch.object(first, "_launch"):
            run = first.submit_json_check(
                self.session["id"], "reviewer", "restart-reference"
            )
        reference = FakeReference()
        restarted = self.jobs(reference)
        record = restarted.get(self.session["id"], "reviewer", run["id"])
        self.assertEqual(record["status"], "uncertain")
        self.assertEqual(len(reference.reconciled), 1)
        self.assertEqual(reference.reconciled[0][0], "prism-m23-run-" + run["id"])
        with self.assertRaises(Denied):
            restarted.submit_json_check(
                self.session["id"], "reviewer", "never-replay-after-restart"
            )

    def test_unconfirmed_cleanup_or_unknown_namespace_resource_blocks_work(self):
        first = self.jobs(recover=False)
        with patch.object(first, "_launch"):
            run = first.submit_json_check(
                self.session["id"], "reviewer", "uncertain-reference"
            )
        failed = self.jobs(FakeReference(fail_cleanup=True))
        self.assertEqual(
            failed.get(self.session["id"], "reviewer", run["id"])["status"],
            "uncertain",
        )
        with self.assertRaises(Denied):
            failed.submit_json_check(
                self.session["id"], "reviewer", "blocked-after-failure"
            )

        with self.store.connect() as db:
            db.execute("UPDATE runs SET status='cancelled' WHERE id=?", (run["id"],))
        foreign = self.jobs(FakeReference(leftovers=["unlabelled-foreign-resource"]))
        self.assertTrue(foreign.registry.blocked)
        with self.assertRaises(Denied) as caught:
            foreign.submit_json_check(
                self.session["id"], "reviewer", "blocked-by-namespace"
            )
        self.assertEqual(caught.exception.status, 503)

    def test_contextual_handoff_preserves_exact_reference_action(self):
        workspace = OwnerWorkspace(self.store)
        workspace.register_project(self.source)
        owner_jobs = Jobs(workspace, recover=False)
        Conversations(workspace, owner_jobs, recover=False)
        actor = OwnerIdentity("owner", "reference-docs")
        chat = workspace.new_conversation(actor, files=["data.json"])
        turn = ident()
        answer = {
            "answer": "The selected synthetic JSON file is ready for review.",
            "claims": [],
            "citations": [],
            "run_references": [],
            "limitations": [],
            "pending_request_id": None,
        }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO turns(id,session,request_key,question,status,answer,created,finished) VALUES(?,?,?,?,?,?,?,?)",
                (
                    turn,
                    chat["id"],
                    ident(),
                    "Review the selected JSON file.",
                    "completed",
                    packed(answer),
                    10,
                    20,
                ),
            )
        selected = chat["manifest"]["files"][0]["id"]
        shared = Handoffs(workspace).freeze(
            chat["id"],
            actor,
            HandoffSelection(
                checkpoint=turn,
                purpose="Share the selected JSON review.",
                summary="One selected JSON document.",
                open_questions="",
                files=[selected],
                runs=[],
                excerpts=[{"turn": turn, "part": "answer"}],
                mode="verify",
            ),
        )
        self.assertEqual(
            packed(shared["manifest"]["action"]), packed(chat["manifest"]["action"])
        )
        self.assertEqual(shared["manifest"]["action"]["profile"], "reference-linux")

    def test_reference_worker_without_trusted_cleanup_record_is_uncertain(self):
        jobs = self.jobs(recover=False)
        with patch.object(jobs, "_launch") as launch:
            run = jobs.submit_json_check(
                self.session["id"], "reviewer", "untrusted-worker-result"
            )
        arguments = launch.call_args.args

        class Process:
            def __init__(self):
                self.pid = os.getpid()
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"not-a-trusted-record")
                self.returncode = 2

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def send_signal(self, signal):
                self.returncode = -signal

            def kill(self):
                self.returncode = -9

        with patch("prism.jobs.subprocess.Popen", return_value=Process()):
            jobs._execute(*arguments)
        record = jobs.get(self.session["id"], "reviewer", run["id"])
        self.assertEqual(record["status"], "uncertain")
        with self.assertRaises(Denied):
            jobs.submit_json_check(
                self.session["id"], "reviewer", "blocked-after-worker-loss"
            )

    def test_synthetic_reference_launch_passes_only_lease_read_fd(self):
        jobs = self.jobs(recover=False)
        with patch.object(jobs, "_launch") as launch:
            run = jobs.submit_json_check(
                self.session["id"], "reviewer", "lease-reference-launch"
            )
        arguments = launch.call_args.args

        class Process:
            def __init__(self):
                self.pid = os.getpid()
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"")
                self.returncode = 2

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        lease_fd = []

        def launched(argv, **options):
            self.assertEqual(argv[1:5], ["-I", "-c", WORKER_BOOTSTRAP, SOURCE_ROOT])
            self.assertEqual(
                argv[-4:-1], ["reference-linux", arguments[8], arguments[9]]
            )
            self.assertEqual(options["pass_fds"], (int(argv[-1]),))
            lease_fd.append(int(argv[-1]))
            self.assertNotIn("preexec_fn", options)
            self.assertEqual(set(options["env"]), {"PATH", "LANG"})
            return Process()

        def attached(*_args):
            self.assertEqual(select.select(lease_fd, [], [], 0)[0], [])

        with (
            patch("prism.jobs.subprocess.Popen", side_effect=launched),
            patch.object(jobs.watchdog, "attach", side_effect=attached),
        ):
            jobs._execute(*arguments)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status,result FROM runs WHERE id=?", (run["id"],)
            ).fetchone()
        self.assertEqual(row["status"], "uncertain")
        self.assertIsNone(row["result"])

    def test_synthetic_reference_job_completes_with_valid_lease(self):
        jobs = self.jobs(recover=False)
        with patch.object(jobs, "_launch") as launch:
            run = jobs.submit_json_check(
                self.session["id"], "reviewer", "lease-valid-result"
            )
        arguments = launch.call_args.args
        payload = {
            "cleaned_up": True,
            "program_sha256": arguments[5],
            "runtime_handler": HANDLER,
            "image_id": jobs.registry.reference.image_id,
            "stop_reason": "exited",
            "exit_code": 0,
            "output_limited": False,
            "stdout": json.dumps(
                {"action": "json-check", "files": arguments[6]["files"]}
            ),
            "guest_kernel": "synthetic-kernel",
            "guest_boot_id": "synthetic-boot",
            "elapsed_seconds": 0.01,
        }

        class Process:
            def __init__(self):
                self.pid = os.getpid()
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(json.dumps(payload).encode())
                self.returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        with patch("prism.jobs.subprocess.Popen", return_value=Process()) as start:
            jobs._execute(*arguments)
        self.assertEqual(len(start.call_args.kwargs["pass_fds"]), 1)
        result = jobs.get(self.session["id"], "reviewer", run["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["output"]["files"], arguments[6]["files"])

    def test_watchdog_release_failure_reclassifies_result_and_event(self):
        jobs = self.jobs(recover=False)
        with patch.object(jobs, "_launch") as launch:
            run = jobs.submit_json_check(
                self.session["id"], "reviewer", "watchdog-release-failure"
            )
        arguments = launch.call_args.args
        payload = {
            "cleaned_up": True,
            "program_sha256": arguments[5],
            "runtime_handler": HANDLER,
            "image_id": jobs.registry.reference.image_id,
            "stop_reason": "exited",
            "exit_code": 0,
            "output_limited": False,
            "stdout": json.dumps(
                {"action": "json-check", "files": arguments[6]["files"]}
            ),
            "guest_kernel": "synthetic-kernel",
            "guest_boot_id": "synthetic-boot",
            "elapsed_seconds": 0.01,
        }

        class Process:
            pid = os.getpid()
            returncode = 0

            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(json.dumps(payload).encode())

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        with (
            patch("prism.jobs.subprocess.Popen", return_value=Process()),
            patch.object(jobs.watchdog, "release", side_effect=WatchdogError("lost")),
        ):
            jobs._execute(*arguments)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status,result FROM runs WHERE id=?", (run["id"],)
            ).fetchone()
            events = list(
                db.execute(
                    "SELECT kind,outcome FROM events WHERE resource=? ORDER BY id",
                    (run["id"],),
                )
            )
        self.assertEqual((row["status"], row["result"]), ("uncertain", None))
        self.assertEqual(events[-1]["kind"], "run_reclassified")
        self.assertEqual(events[-1]["outcome"], "uncertain")

    def test_synthetic_reference_grant_revoke_cancels_only_target_run(self):
        jobs = self.jobs(recover=False)
        principal = NamedPrincipal("https://idp.example/tenant", "lease-recipient")
        other = NamedPrincipal("https://idp.example/tenant", "other-recipient")
        invitation = self.store.create_invitation(
            self.session["version"], principal, mode="verify", expires_in=3600
        )
        other_invitation = self.store.create_invitation(
            self.session["version"], other, mode="verify", expires_in=3600
        )
        session = self.store.redeem_invitation(invitation["token"], principal)
        other_session = self.store.redeem_invitation(other_invitation["token"], other)
        with patch.object(jobs, "_launch") as launch:
            run = jobs.submit_json_check(session["id"], principal, "lease-revoke-run")
        arguments = launch.call_args.args
        payload = {
            "cleaned_up": True,
            "program_sha256": arguments[5],
            "runtime_handler": HANDLER,
            "image_id": jobs.registry.reference.image_id,
            "stop_reason": "cancelled",
        }
        started = threading.Event()
        signalled = threading.Event()

        class Process:
            def __init__(self):
                self.pid = os.getpid()
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(json.dumps(payload).encode())
                self.returncode = None
                started.set()

            def poll(self):
                return self.returncode

            def send_signal(self, _signal):
                self.returncode = 0
                signalled.set()

            def wait(self, timeout=None):
                return self.returncode

        with (
            patch("prism.jobs.subprocess.Popen", side_effect=lambda *a, **k: Process()),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(jobs._execute, *arguments)
            self.assertTrue(started.wait(2))
            self.store.revoke_grant(session["grant_id"])
            future.result(timeout=3)
        self.assertTrue(signalled.is_set())
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status,result FROM runs WHERE id=?", (run["id"],)
            ).fetchone()
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNone(row["result"])
        self.store.session(other_session["id"], other)


class WorkerLeaseSyntheticTests(unittest.TestCase):
    def test_isolated_child_resolves_staged_lease_worker_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "immutable-release" / "src"
            root.mkdir(parents=True)
            installed = Path(__file__).resolve().parents[1] / "src" / "prism"
            shutil.copytree(installed, root / "prism")
            worker_path = root / "prism" / "worker.py"
            probe = WORKER_BOOTSTRAP.replace(
                "runpy.run_module('prism.worker', run_name='__main__', alter_sys=True)",
                "import hashlib, prism.worker; "
                "print(prism.worker.__file__); "
                "print(hashlib.sha256(pathlib.Path(prism.worker.__file__).read_bytes()).hexdigest())",
            )
            self.assertNotEqual(probe, WORKER_BOOTSTRAP)
            base = [sys.executable, "-I", "-c"]
            environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
            located = subprocess.run(
                base + [probe, str(root)],
                cwd="/",
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.splitlines()
            self.assertEqual(
                located,
                [
                    str(worker_path),
                    hashlib.sha256(worker_path.read_bytes()).hexdigest(),
                ],
            )
            # The exact production bootstrap reaches v6 lease validation and
            # rejects a malformed descriptor before any Kata client starts.
            child = subprocess.run(
                base
                + [
                    WORKER_BOOTSTRAP,
                    str(root),
                    "reference-linux-local",
                    "json-check",
                    hashlib.sha256(action_program("json-check").encode()).hexdigest(),
                    "reference-linux",
                    "prism-m23-run-" + "a" * 32,
                    "b" * 32,
                    "invalid-fd",
                ],
                input=b"{}",
                cwd="/",
                env=environment,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(child.returncode, 2)
            self.assertEqual(child.stdout, b"")

    def test_synthetic_worker_cancel_event_reaches_runtime_on_parent_eof(self):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"L")
        entered = threading.Event()

        class FakeRuntime:
            def run(self, action, argument, *, timeout, cancel, name, token):
                entered.set()
                if not cancel.wait(1):
                    raise AssertionError("Lease EOF did not reach the runtime")
                return RunResult(
                    action=action,
                    exit_code=143,
                    stop_reason="cancelled",
                    oom_killed=False,
                    stdout="",
                    stderr="",
                    output_limited=False,
                    cleaned_up=True,
                    elapsed_seconds=0.01,
                    image_id="synthetic-reference",
                    program_sha256=hashlib.sha256(
                        action_program("json-check").encode()
                    ).hexdigest(),
                )

        def break_parent():
            self.assertTrue(entered.wait(1))
            os.close(write_fd)

        breaker = threading.Thread(target=break_parent)
        breaker.start()
        output = io.StringIO()
        with (
            patch.object(
                worker_module.sys,
                "argv",
                [
                    "worker",
                    "reference-linux-local",
                    "json-check",
                    hashlib.sha256(action_program("json-check").encode()).hexdigest(),
                    "reference-linux",
                    "prism-m23-run-" + "a" * 32,
                    "b" * 32,
                    str(read_fd),
                ],
            ),
            patch.object(
                worker_module.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}"))
            ),
            patch.object(worker_module, "ReferenceLinuxRuntime", FakeRuntime),
            patch.object(worker_module.signal, "signal"),
            redirect_stdout(output),
        ):
            self.assertEqual(worker_module.main(), 0)
        breaker.join(timeout=1)
        self.assertEqual(json.loads(output.getvalue())["stop_reason"], "cancelled")

    def test_missing_and_malformed_lease_fail_closed(self):
        for value in ("", "-1", "abc", "9999999", "0", "1", "2"):
            with self.subTest(value=value):
                self.assertFalse(_start_lease_monitor(value, threading.Event()))

    def test_eof_and_expiry_cancel_worker(self):
        for mode in ("eof", "expiry", "malformed"):
            with self.subTest(mode=mode):
                read_fd, write_fd = os.pipe()
                cancelled = threading.Event()
                os.write(write_fd, b"L")
                with patch("prism.worker.LEASE_TTL_SECONDS", 0.15):
                    self.assertTrue(_start_lease_monitor(str(read_fd), cancelled))
                    self.assertFalse(os.get_inheritable(read_fd))
                    if mode == "eof":
                        os.close(write_fd)
                    elif mode == "malformed":
                        os.write(write_fd, b"X")
                    self.assertTrue(cancelled.wait(1))
                if mode == "expiry" or mode == "malformed":
                    os.close(write_fd)


class ReferenceRuntimeUnitTests(unittest.TestCase):
    def test_kata_check_environment_only_adds_fixed_system_paths(self):
        self.assertEqual(KATA_CHECK_ENV["HOME"], "/nonexistent")
        self.assertEqual(KATA_CHECK_ENV["LANG"], ENV["LANG"])
        self.assertEqual(
            KATA_CHECK_ENV["PATH"],
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        )
        self.assertEqual(set(KATA_CHECK_ENV), set(ENV))

        completed = SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with patch(
            "prism.reference_runtime.subprocess.run", return_value=completed
        ) as run:
            _fixed_command(
                Path("/opt/kata/bin/kata-runtime"),
                ["check"],
                env=KATA_CHECK_ENV,
            )
        self.assertEqual(run.call_args.kwargs["env"], KATA_CHECK_ENV)
        self.assertEqual(ENV["PATH"], "/usr/local/bin:/usr/bin:/bin")

    def test_image_metadata_accepts_normalized_repository_name(self):
        digest = IMAGE.split("@", 1)[1]
        metadata = [
            {
                "Id": "sha256:resolved-platform-image",
                "RepoDigests": ["python@" + digest],
                "Config": {"Volumes": None},
            }
        ]
        self.assertEqual(
            _validated_image_metadata(metadata), "sha256:resolved-platform-image"
        )

    def test_image_metadata_rejects_invalid_digest_shapes_and_volumes(self):
        digest = IMAGE.split("@", 1)[1]
        invalid_digests = (
            None,
            [],
            ("python@" + digest,),
            ["python@sha256:" + "0" * 64],
            ["python@" + digest + "-extra"],
            [digest],
            ["python@" + digest, 7],
        )
        for digests in invalid_digests:
            with self.subTest(digests=digests), self.assertRaises(EngineError):
                _validated_image_metadata(
                    {"Id": "sha256:resolved-platform-image", "RepoDigests": digests}
                )
        for metadata in (
            [],
            [{}, {}],
            {"RepoDigests": ["python@" + digest], "Config": []},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(EngineError):
                _validated_image_metadata(metadata)
        with self.assertRaises(EngineError):
            _validated_image_metadata(
                {
                    "Id": "sha256:resolved-platform-image",
                    "RepoDigests": ["python@" + digest],
                    "Config": {"Volumes": {"/data": {}}},
                }
            )

    def test_image_listing_binds_exact_digest_to_strict_image_id(self):
        digest = IMAGE.split("@", 1)[1]
        image_id = "sha256:" + "a" * 64
        listing = b"\n".join(
            (
                json.dumps(
                    {"Digest": "sha256:" + "0" * 64, "ID": "sha256:" + "b" * 64}
                ).encode(),
                json.dumps({"Digest": digest, "ID": image_id}).encode(),
            )
        )
        self.assertEqual(_listed_image_id(listing), image_id)

    def test_image_listing_rejects_invalid_or_ambiguous_bindings(self):
        digest = IMAGE.split("@", 1)[1]
        image_id = "sha256:" + "a" * 64
        row = {"Digest": digest, "ID": image_id}
        invalid = (
            b"",
            b"not-json",
            json.dumps([]).encode(),
            json.dumps({"Digest": digest}).encode(),
            json.dumps({"Digest": digest, "ID": ""}).encode(),
            json.dumps({"Digest": digest, "ID": "A" * 71}).encode(),
            json.dumps({"Digest": digest, "ID": "sha256:" + "a" * 63}).encode(),
            json.dumps({"Digest": digest, "ID": "sha256:" + "A" * 64}).encode(),
            json.dumps({"Digest": "sha256:" + "0" * 64, "ID": image_id}).encode(),
            (json.dumps(row) + "\n" + json.dumps(row)).encode(),
            (json.dumps(row) + "\n").encode() + b"\xff",
        )
        for listing in invalid:
            with self.subTest(listing=listing), self.assertRaises(EngineError):
                _listed_image_id(listing)

    def test_live_host_checks_inspects_only_the_bound_image_id(self):
        digest = IMAGE.split("@", 1)[1]
        lookup_id = "sha256:" + "a" * 64
        inspected_id = "sha256:" + "b" * 64
        listing = json.dumps({"Digest": digest, "ID": lookup_id}).encode()
        metadata = json.dumps(
            [{"Id": inspected_id, "RepoDigests": ["python@" + digest], "Config": {}}]
        ).encode()
        results = [
            SimpleNamespace(returncode=0, stdout=listing, stderr=b""),
            SimpleNamespace(returncode=0, stdout=metadata, stderr=b""),
        ]
        with patch("prism.reference_runtime._command", side_effect=results) as command:
            self.assertEqual(_verified_image_id(), lookup_id)
        self.assertEqual(
            command.call_args_list[0].args[0],
            ["images", "--no-trunc", "--digests", "--format", "{{json .}}"],
        )
        self.assertEqual(
            command.call_args_list[1].args[0], ["image", "inspect", lookup_id]
        )

    def test_verified_image_rejects_invalid_inspected_identity(self):
        digest = IMAGE.split("@", 1)[1]
        listed_id = "sha256:" + "a" * 64
        for inspected_id in (None, "", "sha256:" + "b" * 63, "sha256:" + "B" * 64):
            with self.subTest(inspected_id=inspected_id):
                results = [
                    SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps({"Digest": digest, "ID": listed_id}).encode(),
                        stderr=b"",
                    ),
                    SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            [
                                {
                                    "Id": inspected_id,
                                    "RepoDigests": ["python@" + digest],
                                    "Config": {},
                                }
                            ]
                        ).encode(),
                        stderr=b"",
                    ),
                ]
                with (
                    patch("prism.reference_runtime._command", side_effect=results),
                    self.assertRaises(EngineError),
                ):
                    _verified_image_id()

    def test_operator_exception_report_redacts_untrusted_cause(self):
        cause = OSError(13, "SENTINEL_SECRET", "/private/sentinel-path")
        error = EngineError("The fixed reference runtime host is unavailable.")
        error.__cause__ = cause
        detail = m2_check.bounded_exception(error, {})
        serialized = json.dumps(detail)
        self.assertIn("reference_runtime_host_checks", serialized)
        self.assertIn('"errno=13"', serialized)
        self.assertNotIn("SENTINEL_SECRET", serialized)
        self.assertNotIn("sentinel-path", serialized)
        for item in detail["chain"]:
            self.assertLessEqual(len(item["frames"]), 8)
            self.assertTrue(
                all(
                    set(frame) == {"file", "function", "line"}
                    and frame["file"]
                    in {"reference_runtime.py", "m2-linux-runtime-check.py"}
                    for frame in item["frames"]
                )
            )

        runtime = object.__new__(ReferenceLinuxRuntime)
        with patch(
            "prism.reference_runtime._command",
            side_effect=EngineError("fixed safe failure"),
        ):
            try:
                runtime.all_resources()
            except EngineError as traced:
                traced_detail = m2_check.bounded_exception(
                    traced, {"host_checks_completed": True}
                )
        self.assertTrue(traced_detail["chain"][0]["frames"])
        self.assertEqual(
            traced_detail["chain"][0]["frames"][-1]["function"], "all_resources"
        )

    def test_operator_exception_report_distinguishes_initial_stages(self):
        error = EngineError("fixed safe error")
        self.assertEqual(
            m2_check.bounded_exception(error, {})["stage"],
            "reference_runtime_host_checks",
        )
        self.assertEqual(
            m2_check.bounded_exception(error, {"host_checks_completed": True})["stage"],
            "initial_namespace_inspection",
        )
        self.assertEqual(
            m2_check.bounded_exception(
                error,
                {"host_checks_completed": True, "initial_namespace_empty": True},
            )["stage"],
            "integration_exercise",
        )

    def test_fixed_command_errors_do_not_disclose_captured_output(self):
        failed = SimpleNamespace(
            returncode=23,
            stdout=b"",
            stderr=b"SENTINEL_SECRET /private/sentinel-path",
        )
        with patch("prism.reference_runtime.subprocess.run", return_value=failed):
            for invoke in (
                lambda: _command(["info"]),
                lambda: _fixed_command(Path("/opt/kata/bin/kata-runtime"), ["check"]),
            ):
                with self.assertRaises(EngineError) as caught:
                    invoke()
                message = str(caught.exception)
                self.assertIn("exit=23", message)
                self.assertNotIn("SENTINEL_SECRET", message)
                self.assertNotIn("sentinel-path", message)

    def test_namespace_name_listing_keeps_all_without_quiet_format_conflict(self):
        runtime = object.__new__(ReferenceLinuxRuntime)
        with patch(
            "prism.reference_runtime._command",
            return_value=SimpleNamespace(stdout=b"prism-m23-z\nprism-m23-a\n"),
        ) as command:
            self.assertEqual(runtime.all_resources(), ["prism-m23-a", "prism-m23-z"])
        command.assert_called_once_with(["ps", "-a", "--format", "{{.Names}}"])

        with patch(
            "prism.reference_runtime._command",
            return_value=SimpleNamespace(stdout=b"prism-m23-run-b\nprism-m23-run-a\n"),
        ) as command:
            self.assertEqual(
                runtime.owned_resources(),
                ["prism-m23-run-a", "prism-m23-run-b"],
            )
        command.assert_called_once_with(
            [
                "ps",
                "-a",
                "--filter",
                "label=" + LABEL,
                "--format",
                "{{.Names}}",
            ]
        )

    def test_inspect_fallback_keeps_quiet_without_custom_format(self):
        runtime = object.__new__(ReferenceLinuxRuntime)
        name = "prism-m23-run-" + "a" * 32
        token = "b" * 32
        results = [
            SimpleNamespace(returncode=1, stdout=b"", stderr=b""),
            SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
        ]
        with patch("prism.reference_runtime._command", side_effect=results) as command:
            self.assertIsNone(runtime.inspect_owned(name, token))
        self.assertEqual(
            command.call_args_list[1].args[0],
            ["ps", "-aq", "--filter", "name=" + name],
        )
        self.assertNotIn("--format", command.call_args_list[1].args[0])

    def test_fixed_argv_has_kata_and_no_mount_network_or_privilege_escape(self):
        argv = reference_argv(
            "print(1)", ["bounded"], "prism-m23-run-" + "a" * 32, "b" * 32
        )
        joined = " ".join(argv)
        self.assertIn("--runtime " + HANDLER, joined)
        self.assertIn("--network none", joined)
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("--ulimit nproc=32:32", joined)
        self.assertIn("--label " + LABEL + "=" + "b" * 32, joined)
        self.assertIn(IMAGE, argv)
        self.assertNotIn("--privileged", argv)
        self.assertNotIn("--mount", argv)
        self.assertNotIn("--volume", argv)

    def test_kata_policy_requires_exact_unambiguous_values(self):
        _validate_kata_config({"runtime": dict(KATA_POLICY)})
        changed = dict(KATA_POLICY, disable_guest_seccomp=True)
        with self.assertRaises(EngineError):
            _validate_kata_config({"runtime": changed})
        with self.assertRaises(EngineError):
            _validate_kata_config({"one": dict(KATA_POLICY), "two": dict(KATA_POLICY)})

    def test_run_keeps_typed_guest_provenance_in_serialized_result(self):
        with patch(
            "prism.reference_runtime._live_host_checks", return_value="sha256:reference"
        ):
            runtime = ReferenceLinuxRuntime()
        output = (
            json.dumps({"guest_kernel": "guest-kernel", "guest_boot_id": "guest-boot"})
            + "\n"
            + json.dumps({"action": "json-check", "files": []})
        )
        raw = RunResult(
            "json-check",
            0,
            "exited",
            False,
            output,
            "",
            False,
            True,
            1.0,
            "sha256:reference",
            "program-hash",
        )
        with patch.object(runtime, "_execute", return_value=raw) as execute:
            result = runtime.run(
                "json-check", "e30=", name="prism-m23-run-" + "a" * 32, token="b" * 32
            )
        record = result.as_dict()
        self.assertEqual(record["runtime_handler"], HANDLER)
        self.assertEqual(record["guest_kernel"], "guest-kernel")
        self.assertEqual(record["guest_boot_id"], "guest-boot")
        self.assertEqual(json.loads(record["stdout"])["action"], "json-check")
        self.assertEqual(execute.call_args.kwargs["name"], "prism-m23-run-" + "a" * 32)
        self.assertNotEqual(record["guest_kernel"], platform.release())

    def test_cancel_before_guest_creation_returns_trusted_clean_record(self):
        with patch(
            "prism.reference_runtime._live_host_checks", return_value="sha256:reference"
        ):
            runtime = ReferenceLinuxRuntime()

        class Cancelled:
            @staticmethod
            def is_set():
                return True

        with patch.object(runtime, "_execute") as execute:
            result = runtime.run("json-check", "e30=", cancel=Cancelled())
        execute.assert_not_called()
        self.assertEqual(result.stop_reason, "cancelled")
        self.assertTrue(result.cleaned_up)
        self.assertEqual(result.runtime_handler, HANDLER)
        self.assertIsNone(result.guest_boot_id)


if __name__ == "__main__":
    unittest.main()
