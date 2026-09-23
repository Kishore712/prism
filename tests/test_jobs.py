import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from prism.jobs import Jobs
from prism.sharing import DEMO_FILES, Denied, Source, Store, prepare_source


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


if __name__ == "__main__":
    unittest.main()
