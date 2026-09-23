from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path


class DurableCliEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]
        cls.sample = cls.root / "examples" / "chatgpt" / "synthetic_export.json"

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.root / "src")
        return subprocess.run(
            [sys.executable, "-m", "prism", *arguments],
            cwd=self.root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_capture_select_preview_and_resume_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = self._run(
                "capture",
                "list",
                str(self.sample),
                "--data-dir",
                directory,
                "--json",
            )
            conversation_ref = json.loads(inventory.stdout)["conversations"][0][
                "conversation_ref"
            ]
            imported = self._run(
                "capture",
                "import",
                str(self.sample),
                "--conversation-ref",
                conversation_ref,
                "--data-dir",
                directory,
                "--json",
            )
            result = json.loads(imported.stdout)
            draft_id = result["draft_id"]

            selected = self._run(
                "draft",
                "select",
                draft_id,
                "--turns",
                "2",
                "--data-dir",
                directory,
                "--json",
            )
            selected_state = json.loads(selected.stdout)
            previewed = self._run(
                "draft",
                "preview",
                draft_id,
                "--data-dir",
                directory,
                "--json",
            )
            preview = json.loads(previewed.stdout)
            resumed = self._run(
                "draft",
                "show",
                draft_id,
                "--data-dir",
                directory,
                "--json",
            )
            review = json.loads(resumed.stdout)

            self.assertTrue((Path(directory) / "prism.db").exists())
            self.assertEqual(selected_state["revision"], 2)
            self.assertEqual(
                [message["content"] for message in preview["snapshot"]["messages"]],
                [
                    "How should Prism treat files mentioned in the conversation?",
                    "Record safe file metadata only. The owner must explicitly supply and select file bytes in a later phase.",
                ],
            )
            self.assertEqual(review["draft"]["previewed_revision"], 2)
            self.assertEqual(review["draft"]["preview_hash"], preview["preview_hash"])

            checked = self._run("db", "check", "--data-dir", directory)
            backed_up = self._run("db", "backup", "--data-dir", directory)
            self.assertIn("integrity check: ok", checked.stdout)
            self.assertIn("Database backup created:", backed_up.stdout)
            self.assertEqual(len(list((Path(directory) / "backups").glob("*.db"))), 1)

    def test_phase5_publish_share_redeem_verify_and_revoke_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = json.loads(
                self._run(
                    "capture",
                    "list",
                    str(self.sample),
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            imported = json.loads(
                self._run(
                    "capture",
                    "import",
                    str(self.sample),
                    "--conversation-ref",
                    inventory["conversations"][0]["conversation_ref"],
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            draft_id = imported["draft_id"]
            selection = json.loads(
                self._run(
                    "draft",
                    "select",
                    draft_id,
                    "--turns",
                    "1",
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            preview = json.loads(
                self._run(
                    "draft",
                    "preview",
                    draft_id,
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            publication = json.loads(
                self._run(
                    "draft",
                    "publish",
                    draft_id,
                    "--expected-revision",
                    str(selection["revision"]),
                    "--expected-preview-hash",
                    preview["preview_hash"],
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            share = json.loads(
                self._run(
                    "share",
                    "create",
                    publication["snapshot_id"],
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            invitation = json.loads(
                self._run(
                    "invitation",
                    "create",
                    share["share"]["share_id"],
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            grant = json.loads(
                self._run(
                    "invitation",
                    "redeem",
                    "--token",
                    invitation["invitation_token"],
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            authorization = json.loads(
                self._run(
                    "grant",
                    "verify",
                    "--token",
                    grant["grant_token"],
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            listed_invitations = json.loads(
                self._run(
                    "invitations",
                    "list",
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            listed_grants = json.loads(
                self._run(
                    "grants",
                    "list",
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )

            self.assertEqual(
                authorization["snapshot"]["snapshot_id"],
                publication["snapshot_id"],
            )
            self.assertEqual(authorization["share_version"]["version"], 1)
            self.assertEqual(
                listed_invitations[0]["invitation_id"],
                invitation["invitation"]["invitation_id"],
            )
            self.assertEqual(listed_grants[0]["grant_id"], grant["grant"]["grant_id"])
            with closing(sqlite3.connect(Path(directory) / "prism.db")) as connection:
                stored = " ".join(
                    row[0]
                    for row in connection.execute(
                        "SELECT token_hash FROM invitations UNION ALL "
                        "SELECT token_hash FROM grants"
                    )
                )
            self.assertNotIn(invitation["invitation_token"], stored)
            self.assertNotIn(grant["grant_token"], stored)

            revoked = json.loads(
                self._run(
                    "snapshot",
                    "revoke",
                    publication["snapshot_id"],
                    "--purge",
                    "--data-dir",
                    directory,
                    "--json",
                ).stdout
            )
            self.assertEqual(revoked["summary"]["status"], "purged")
            self.assertIsNone(revoked["content"])


if __name__ == "__main__":
    unittest.main()
