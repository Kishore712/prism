"""Owner-only, grant-scoped active-job observation (synthetic runtime)."""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from prism.identity import OIDCAuth, OIDCConfig
from prism.jobs import Jobs
from prism.sharing import NamedPrincipal, Source, Store, digest, prepare_source
from prism.webapp import create_app


class ReferenceProbe:
    def __init__(self):
        self.state = None
        self.calls = []

    def inspect_owned(self, resource, token):
        self.calls.append((resource, token))
        return {"State": {"Status": self.state}} if self.state else None


class Registry:
    profile = "development"
    blocked = False

    def __init__(self):
        self.reference = ReferenceProbe()

    def assert_ready(self, profile):
        return None


class OwnerGrantTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        prepare_source(root / "source")
        self.store = Store(root / "state.sqlite")
        self.source = Source(root / "source")
        version = self.store.candidate(
            self.source.freeze(
                ["observations.csv", "baseline.json", "limitations.md"],
                "Grant status fixture",
                "verify",
            )
        )
        self.store.approve(version["id"], version["digest"])
        self.registry = Registry()
        self.auth = OIDCAuth(
            self.store,
            OIDCConfig(
                issuer="https://idp.example/tenant",
                authorization_endpoint="https://idp.example/authorize",
                token_endpoint="https://idp.example/token",
                jwks_uri="https://idp.example/jwks",
                client_id="prism-client",
                redirect_uri="https://prism.example/auth/oidc/callback",
                public_origin="https://prism.example",
                owner_subject="owner-subject",
                algorithms=("RS256",),
            ),
        )
        jobs = Jobs(self.store, registry=self.registry)
        self.app = create_app(self.store, self.source, auth=self.auth, jobs=jobs)
        self.owner = TestClient(self.app, base_url="https://prism.example")
        self.reviewer = TestClient(self.app, base_url="https://prism.example")
        self.anonymous = TestClient(self.app, base_url="https://prism.example")
        now = time.time()
        with self.store.connect() as db:
            for client, cookie, subject, role in (
                (self.owner, "owner-cookie", "owner-subject", "owner"),
                (self.reviewer, "review-cookie", "recipient-a", "review"),
            ):
                db.execute(
                    "INSERT INTO identity_sessions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        digest(cookie),
                        self.auth.config.issuer,
                        subject,
                        role,
                        None,
                        "csrf",
                        now + 600,
                        now,
                    ),
                )
                client.cookies.set(self.auth.cookie_name, cookie, path="/")
        self.grants = []
        self.sessions = []
        for subject in ("recipient-a", "recipient-b"):
            invitation = self.store.create_invitation(
                version["id"],
                NamedPrincipal(self.auth.config.issuer, subject),
                mode="verify",
                expires_in=600,
            )
            session = self.store.redeem_invitation(
                invitation["token"], NamedPrincipal(self.auth.config.issuer, subject)
            )
            self.grants.append(session["grant_id"])
            self.sessions.append(session["id"])

    def task(self, client=None):
        return (client or self.owner).get("/api/owner/active-task")

    def insert_run(self, index, status):
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO runs(id,session,request_key,seed,status,created,"
                "runtime_profile,runtime_resource,runtime_token) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    f"run-{index}",
                    self.sessions[index],
                    f"request-{index}",
                    0,
                    status,
                    time.time(),
                    "reference-linux",
                    f"resource-{index}",
                    f"token-{index}",
                ),
            )

    def test_owner_auth_and_exact_grant_scope(self):
        self.insert_run(0, "running")
        self.registry.reference.state = "running"
        self.assertEqual(self.task(self.anonymous).status_code, 401)
        self.assertEqual(self.task(self.reviewer).status_code, 401)
        self.assertEqual(
            self.task().json(),
            {
                "grant_id": self.grants[0],
                "status": "running",
                "owned_running": True,
            },
        )
        self.assertEqual(self.registry.reference.calls, [("resource-0", "token-0")])
        self.assertNotIn("token-0", self.task().text)
        self.assertNotIn(self.grants[1], self.task().text)

    def test_status_lifecycle_and_failed_runtime_observation(self):
        self.assertEqual(self.task().json()["status"], "idle")
        self.insert_run(0, "queued")
        self.assertEqual(
            self.task().json(),
            {
                "grant_id": self.grants[0],
                "status": "queued",
                "owned_running": False,
            },
        )
        with self.store.connect() as db:
            db.execute("UPDATE runs SET status='running' WHERE id='run-0'")
        self.assertEqual(self.task().json()["owned_running"], False)
        self.registry.reference.state = "running"
        self.assertEqual(self.task().json()["owned_running"], True)
        self.registry.reference.inspect_owned = lambda *args: (_ for _ in ()).throw(
            ValueError("unavailable")
        )
        self.assertEqual(self.task().json()["owned_running"], None)
        with self.store.connect() as db:
            db.execute("UPDATE runs SET status='completed' WHERE id='run-0'")
        self.assertEqual(
            self.task().json(),
            {"grant_id": None, "status": "idle", "owned_running": False},
        )

    def test_unrelated_grant_is_the_only_reported_grant(self):
        self.insert_run(1, "running")
        self.registry.reference.state = "running"
        self.assertEqual(self.task().json()["grant_id"], self.grants[1])
        self.assertEqual(self.registry.reference.calls, [("resource-1", "token-1")])

    def test_completed_during_inspection_returns_idle(self):
        self.insert_run(0, "running")

        def complete_during_inspect(resource, token):
            with self.store.connect() as db:
                db.execute("UPDATE runs SET status='completed' WHERE id='run-0'")
            return {"State": {"Status": "running"}}

        self.registry.reference.inspect_owned = complete_during_inspect
        self.assertEqual(
            self.task().json(),
            {"grant_id": None, "status": "idle", "owned_running": False},
        )


if __name__ == "__main__":
    unittest.main()
