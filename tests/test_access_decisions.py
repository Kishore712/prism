"""Synthetic, no-model checks for owner decisions on named access requests."""

import concurrent.futures
import tempfile
import time
import unittest
from pathlib import Path

from prism.conversation import Conversations
from prism.jobs import Jobs
from prism.sharing import Denied, NamedPrincipal, Source, Store, prepare_source


class AccessDecisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        prepare_source(root / "source")
        self.source = Source(root / "source")
        self.store = Store(root / "state.sqlite")
        self.jobs = Jobs(self.store)
        self.addCleanup(self.jobs.shutdown)
        self.conversations = Conversations(self.store, self.jobs)
        self.recipient = NamedPrincipal("https://idp.example/tenant", "recipient-one")
        self.other = NamedPrincipal("https://idp.example/tenant", "recipient-two")
        self.inspect = self._version(["overview.md"], "Original review", "inspect")
        self.expanded = self._version(
            ["observations.csv", "baseline.json", "limitations.md"],
            "Expanded review",
            "verify",
        )
        original = self.store.create_invitation(
            self.inspect, self.recipient, mode="inspect", expires_in=3600
        )
        self.session = self.store.redeem_invitation(original["token"], self.recipient)
        self.request = self.conversations.request_access(
            self.session["id"], self.recipient, "Please allow the reviewed validation."
        )["id"]

    def _version(self, files, purpose, mode):
        candidate = self.store.candidate(
            self.source.freeze(files, purpose, mode),
            project_id="paired-evaluation",
        )
        self.store.approve(candidate["id"], candidate["digest"])
        return candidate["id"]

    def test_approval_is_a_new_bound_invitation_and_original_stays_inspect(self):
        decision = self.store.decide_access_request(
            self.request,
            decision="approve",
            version=self.expanded,
            mode="verify",
            expires_in=300,
        )
        self.assertEqual(decision["status"], "approved")
        self.assertEqual(decision["action"], "bootstrap")
        self.assertEqual(
            self.store.session(self.session["id"], self.recipient)["mode"], "inspect"
        )
        status = self.conversations.requests(self.session["id"], self.recipient)[0]
        self.assertEqual(status["decision_version"], self.expanded)
        self.assertNotIn("token", status)
        with self.assertRaises(Denied):
            self.store.redeem_invitation(decision["token"], self.other)
        expanded_session = self.store.redeem_invitation(
            decision["token"], self.recipient
        )
        self.assertEqual(expanded_session["mode"], "verify")
        self.assertNotEqual(expanded_session["id"], self.session["id"])
        self.assertEqual(expanded_session["version"], self.expanded)
        with self.store.connect() as db:
            events = [
                row["outcome"]
                for row in db.execute(
                    "SELECT outcome FROM events WHERE kind='access_decided' AND resource=?",
                    (self.request,),
                )
            ]
        self.assertEqual(events, ["approved"])

    def test_denial_is_terminal_and_creates_no_invitation(self):
        before = len(self.store.invitations())
        self.assertEqual(
            self.store.decide_access_request(self.request, decision="deny")["status"],
            "denied",
        )
        self.assertEqual(len(self.store.invitations()), before)
        self.assertEqual(
            self.conversations.requests(self.session["id"], self.recipient)[0][
                "status"
            ],
            "denied",
        )
        with self.assertRaises(Denied):
            self.store.decide_access_request(
                self.request,
                decision="approve",
                version=self.expanded,
                mode="verify",
                expires_in=300,
            )

    def test_invalid_approval_keeps_request_pending_and_no_invitation(self):
        before = len(self.store.invitations())
        alternate = self.store.candidate(
            {
                **self.source.freeze(["overview.md"], "Other project", "inspect"),
                "project": "other-project",
            },
            project_id="other-project",
        )
        self.store.approve(alternate["id"], alternate["digest"])
        for version, mode in (
            (alternate["id"], "inspect"),
            (self.inspect, "verify"),
        ):
            with self.assertRaises(Denied):
                self.store.decide_access_request(
                    self.request,
                    decision="approve",
                    version=version,
                    mode=mode,
                    expires_in=300,
                )
        self.assertEqual(len(self.store.invitations()), before)
        self.assertEqual(
            self.conversations.requests(self.session["id"], self.recipient)[0][
                "status"
            ],
            "pending",
        )

    def test_distinct_projects_with_same_title_cannot_cross_approve(self):
        same_title = self.store.candidate(
            self.source.freeze(["overview.md"], "Same title, different ID", "inspect"),
            project_id="another-project",
        )
        self.store.approve(same_title["id"], same_title["digest"])
        self.assertEqual(
            self.store.owner_version(same_title["id"])["manifest"]["project"],
            self.store.owner_version(self.inspect)["manifest"]["project"],
        )
        owner_versions = {item["id"]: item for item in self.store.versions(owner=True)}
        self.assertEqual(owner_versions[self.expanded]["mode"], "verify")
        self.assertNotEqual(
            owner_versions[same_title["id"]]["project_id"],
            owner_versions[self.inspect]["project_id"],
        )
        with self.assertRaises(Denied):
            self.store.decide_access_request(
                self.request,
                decision="approve",
                version=same_title["id"],
                mode="inspect",
                expires_in=300,
            )

    def test_source_grant_revocation_blocks_approval_and_unredeemed_invitation(self):
        grant = self.session["grant_id"]
        self.store.revoke_grant(grant)
        with self.assertRaises(Denied):
            self.store.decide_access_request(
                self.request,
                decision="approve",
                version=self.expanded,
                mode="verify",
                expires_in=300,
            )

    def test_source_version_revocation_blocks_approval(self):
        self.store.revoke(self.inspect)
        with self.assertRaises(Denied):
            self.store.decide_access_request(
                self.request,
                decision="approve",
                version=self.expanded,
                mode="verify",
                expires_in=300,
            )

    def test_source_session_expiry_blocks_approval(self):
        with self.store.connect() as db:
            db.execute(
                "UPDATE sessions SET expires=? WHERE id=?",
                (time.time() - 1, self.session["id"]),
            )
        with self.assertRaises(Denied):
            self.store.decide_access_request(
                self.request,
                decision="approve",
                version=self.expanded,
                mode="verify",
                expires_in=300,
            )

    def test_revocation_after_approval_blocks_activation_and_redemption(self):
        decision = self.store.decide_access_request(
            self.request,
            decision="approve",
            version=self.expanded,
            mode="verify",
            expires_in=300,
        )
        self.store.revoke_grant(self.session["grant_id"])
        with self.assertRaises(Denied):
            self.store.invitation_activation(decision["invitation"])
        with self.assertRaises(Denied):
            self.store.redeem_invitation(decision["token"], self.recipient)

    def test_source_version_revocation_blocks_unredeemed_approval(self):
        decision = self.store.decide_access_request(
            self.request,
            decision="approve",
            version=self.expanded,
            mode="verify",
            expires_in=300,
        )
        self.store.revoke(self.inspect)
        with self.assertRaises(Denied):
            self.store.redeem_invitation(decision["token"], self.recipient)

    def test_redeemed_followup_grant_has_separate_lifecycle(self):
        decision = self.store.decide_access_request(
            self.request,
            decision="approve",
            version=self.expanded,
            mode="verify",
            expires_in=300,
        )
        followup = self.store.redeem_invitation(decision["token"], self.recipient)
        self.store.revoke_grant(self.session["grant_id"])
        self.assertEqual(
            self.store.session(followup["id"], self.recipient)["mode"], "verify"
        )
        self.store.revoke_grant(followup["grant_id"])
        with self.assertRaises(Denied):
            self.store.session(followup["id"], self.recipient)

    def test_expired_source_and_concurrent_decision_fail_closed(self):
        with self.store.connect() as db:
            db.execute(
                "UPDATE grants SET expires=? WHERE id=?",
                (time.time() + 299, self.session["grant_id"]),
            )
        with self.assertRaises(Denied):
            self.store.decide_access_request(
                self.request,
                decision="approve",
                version=self.expanded,
                mode="verify",
                expires_in=300,
            )
        with self.store.connect() as db:
            db.execute(
                "UPDATE grants SET expires=? WHERE id=?",
                (time.time() + 3600, self.session["grant_id"]),
            )

        def decide():
            try:
                return self.store.decide_access_request(self.request, decision="deny")
            except Denied:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: decide(), range(2)))
        self.assertEqual(sum(item is not None for item in outcomes), 1)
        self.assertEqual(len(self.store.invitations()), 1)

    def test_competing_approve_and_deny_commit_one_terminal_decision(self):
        def attempt(decision):
            try:
                return self.store.decide_access_request(
                    self.request,
                    decision=decision,
                    **(
                        {"version": self.expanded, "mode": "verify", "expires_in": 300}
                        if decision == "approve"
                        else {}
                    ),
                )
            except Denied:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ("approve", "deny")))
        committed = [item for item in outcomes if item]
        self.assertEqual(len(committed), 1)
        status = self.conversations.requests(self.session["id"], self.recipient)[0][
            "status"
        ]
        self.assertEqual(status, committed[0]["status"])
        self.assertEqual(len(self.store.invitations()), 1 + int(status == "approved"))


if __name__ == "__main__":
    unittest.main()
