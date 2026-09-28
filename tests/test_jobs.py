import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from prism.jobs import GLOBAL_DEMO_RUN_LIMIT, Jobs
from prism.sharing import (
    DEMO_FILES,
    Denied,
    NamedPrincipal,
    Source,
    Store,
    prepare_source,
)


class JobPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        prepare_source(root / "source")
        self.store = Store(root / "state.sqlite")
        candidate = self.store.candidate(
            Source(root / "source").freeze(
                [n for n in DEMO_FILES if n != "private-notes.txt"],
                "Review the synthetic experiment",
                "verify",
            )
        )
        self.store.approve(candidate["id"], candidate["digest"])
        self.session = self.store.new_session(candidate["id"], "reviewer")
        self.jobs = Jobs(self.store)
        # Policy-only tests deliberately do not start a worker or use Docker.
        self.patch = patch.object(Jobs, "_launch")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def submit(self, seed=23, key="logical-request"):
        return self.jobs.submit(self.session["id"], "reviewer", seed, key)

    def finish(self):
        with self.store.connect() as db:
            db.execute("UPDATE runs SET status='completed' WHERE status='queued'")

    def test_invalid_types_injection_inspect_and_cross_session_denied(self):
        for value in (-1, 1001, True, 1.5, "23; echo canary", None):
            with self.subTest(value=value), self.assertRaises(Denied):
                self.submit(value)
        observer = self.store.new_session(self.session["version"], "observer")
        with self.assertRaises(Denied):
            self.jobs.submit(observer["id"], "observer", 23, "observer-request")
        one = self.submit()
        two = self.store.new_session(self.session["version"], "reviewer")
        with self.assertRaises(Denied):
            self.jobs.get(two["id"], "reviewer", one["id"])

    def test_idempotency_conflicts_and_six_run_allowance(self):
        first = self.submit()
        self.assertEqual(first["id"], self.submit()["id"])
        with self.assertRaises(Denied):
            self.submit(24)
        self.finish()
        for n in range(5):
            self.submit(n, f"run-number-{n}")
            self.finish()
        with self.assertRaises(Denied) as error:
            self.submit(42, "over-budget")
        self.assertEqual(error.exception.status, 429)

    def test_global_demo_run_limit_allows_25th_and_32nd_then_denies(self):
        self.assertEqual(GLOBAL_DEMO_RUN_LIMIT, 32)

        def add_completed(start, stop):
            with self.store.connect() as db:
                for number in range(start, stop):
                    db.execute(
                        "INSERT INTO runs(id,session,request_key,seed,status,created) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            f"{number:032x}",
                            "historical-synthetic-session",
                            f"historical-{number}",
                            0,
                            "completed",
                            float(number),
                        ),
                    )

        add_completed(1, 25)
        self.submit(25, "global-run-25")
        self.finish()
        add_completed(25, 31)
        self.submit(32, "global-run-32")
        self.finish()
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 32)
        with self.assertRaises(Denied) as error:
            self.submit(33, "global-run-33")
        self.assertEqual(error.exception.status, 429)

    def test_concurrent_reservations_allow_only_one_job(self):
        def attempt(n):
            try:
                self.submit(n, f"parallel-{n}")
                return 1
            except Denied:
                return 0

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(16))), 1)

    def test_restart_uncertainty_and_revocation_fail_closed(self):
        first = self.submit()
        restarted = Jobs(self.store)
        self.assertEqual(
            restarted.get(self.session["id"], "reviewer", first["id"])["status"],
            "uncertain",
        )
        with self.assertRaises(Denied) as error:
            restarted.submit(self.session["id"], "reviewer", 42, "retry-after-restart")
        self.assertEqual(error.exception.status, 503)
        self.store.revoke(self.session["version"])
        with self.assertRaises(Denied):
            restarted.get(self.session["id"], "reviewer", first["id"])

    def _named_session(self):
        recipient = NamedPrincipal("https://idp.example/tenant", "job-recipient")
        invitation = self.store.create_invitation(
            self.session["version"], recipient, mode="verify", expires_in=3600
        )
        session = self.store.redeem_invitation(invitation["token"], recipient)
        return session, recipient

    def _execute_fake_worker(self, session, actor, *, after_result=None):
        run = self.jobs.submit(session["id"], actor, 23, "revoke-race-23")
        action = session["manifest"]["action"]
        payload = {
            "cleaned_up": True,
            "program_sha256": action["program_sha256"],
            "stop_reason": "exited",
            "exit_code": 0,
            "output_limited": False,
            "stdout": json.dumps({"seed": 23, "synthetic": True}),
            "image_id": "synthetic-runtime-double",
            "elapsed_seconds": 0.01,
        }

        class FakeProcess:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(json.dumps(payload).encode())
                self.returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        from prism import jobs as jobs_module

        original_digest = jobs_module.digest

        def digest_after_result(value):
            if after_result:
                after_result()
            return original_digest(value)

        with (
            patch("prism.jobs.subprocess.Popen", return_value=FakeProcess()),
            patch("prism.jobs.digest", side_effect=digest_after_result),
        ):
            self.jobs._execute(
                run["id"],
                session["id"],
                actor,
                "bootstrap",
                "23",
                action["program_sha256"],
                {"seed": 23},
                "development",
                None,
                None,
            )
        return run["id"]

    def test_named_authorized_work_can_complete_with_trusted_cleanup(self):
        session, recipient = self._named_session()
        run = self._execute_fake_worker(session, recipient)
        record = self.jobs.get(session["id"], recipient, run)
        self.assertEqual(record["status"], "completed")
        self.assertTrue(record["result"]["cleaned_up"])

    def test_revoke_during_result_validation_discards_run(self):
        for revoke_kind in ("grant", "version"):
            with self.subTest(revoke_kind=revoke_kind):
                session, recipient = self._named_session()
                reached, release = threading.Event(), threading.Event()

                def pause_after_result(reached=reached, release=release):
                    reached.set()
                    self.assertTrue(release.wait(3))

                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        self._execute_fake_worker,
                        session,
                        recipient,
                        after_result=pause_after_result,
                    )
                    self.assertTrue(reached.wait(3))
                    if revoke_kind == "grant":
                        self.store.revoke_grant(session["grant_id"])
                    else:
                        self.store.revoke(session["version"])
                    release.set()
                    run = future.result(timeout=5)
                with self.store.connect() as db:
                    row = db.execute(
                        "SELECT status,result,error FROM runs WHERE id=?", (run,)
                    ).fetchone()
                self.assertEqual(row["status"], "cancelled")
                self.assertIsNone(row["result"])
                self.assertIn("cleanup were confirmed", row["error"])
                with self.assertRaises(Denied):
                    self.jobs.get(session["id"], recipient, run)

    def test_queued_run_revoked_before_launch_never_starts_worker(self):
        session, recipient = self._named_session()
        run = self.jobs.submit(session["id"], recipient, 23, "queued-revoke-23")
        self.store.revoke_grant(session["grant_id"])
        action = session["manifest"]["action"]
        with patch("prism.jobs.subprocess.Popen") as launch:
            self.jobs._execute(
                run["id"],
                session["id"],
                recipient,
                "bootstrap",
                "23",
                action["program_sha256"],
                {"seed": 23},
                "development",
                None,
                None,
            )
        launch.assert_not_called()
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status,result FROM runs WHERE id=?", (run["id"],)
            ).fetchone()
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNone(row["result"])

    def test_running_revoke_signals_worker_and_requires_cleanup_proof(self):
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed):
                session, recipient = self._named_session()
                run = self.jobs.submit(
                    session["id"], recipient, 23, f"running-revoke-{confirmed}"
                )
                action = session["manifest"]["action"]
                started, signalled = threading.Event(), threading.Event()
                payload = {
                    "cleaned_up": True,
                    "program_sha256": action["program_sha256"],
                    "stop_reason": "cancelled",
                    "exit_code": 143,
                    "output_limited": False,
                    "stdout": "",
                    "image_id": "synthetic-runtime-double",
                    "elapsed_seconds": 0.01,
                }

                class RunningProcess:
                    def __init__(self, payload=payload, started=started):
                        self.stdin = io.BytesIO()
                        self.stdout = io.BytesIO(json.dumps(payload).encode())
                        self.returncode = None
                        started.set()

                    def poll(self):
                        return self.returncode

                    def send_signal(
                        self, sig, signalled=signalled, confirmed=confirmed
                    ):
                        signalled.set()
                        self.returncode = 0 if confirmed else 3

                    def wait(self, timeout=None):
                        return self.returncode

                with (
                    patch(
                        "prism.jobs.subprocess.Popen",
                        side_effect=lambda *a, **k: RunningProcess(),
                    ),
                    ThreadPoolExecutor(max_workers=1) as pool,
                ):
                    future = pool.submit(
                        self.jobs._execute,
                        run["id"],
                        session["id"],
                        recipient,
                        "bootstrap",
                        "23",
                        action["program_sha256"],
                        {"seed": 23},
                        "development",
                        None,
                        None,
                    )
                    self.assertTrue(started.wait(3))
                    self.store.revoke_grant(session["grant_id"])
                    future.result(timeout=5)
                self.assertTrue(signalled.is_set())
                with self.store.connect() as db:
                    row = db.execute(
                        "SELECT status,result FROM runs WHERE id=?", (run["id"],)
                    ).fetchone()
                self.assertEqual(
                    row["status"], "cancelled" if confirmed else "uncertain"
                )
                self.assertIsNone(row["result"])


if __name__ == "__main__":
    unittest.main()
