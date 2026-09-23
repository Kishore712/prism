from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from prism import cli
from prism.database import PrismDatabase
from prism.providers.chatgpt.shared_link import ChatGPTSharedLinkAdapter
from prism.services.drafts import DraftService
from prism.services.durable_shared_link_capture import DurableSharedLinkCaptureService
from prism.services.normalization import CaptureNormalizer
from prism.services.publication import PublicationService
from prism.services.sharing import SharingService

from tests.providers.chatgpt.shared_link.helpers import StaticFetcher, synthetic_shared_page

SHARE_URL = "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"


def _session(path: Path) -> None:
    records = []
    for index, (question, answer) in enumerate(
        [("First question about design?", "First answer."), ("Private question QSECRET?", "Private answer.")],
        start=1,
    ):
        records.append({"type": "user", "uuid": f"u{index}", "message": {"role": "user", "content": question}})
        records.append(
            {"type": "assistant", "uuid": f"a{index}",
             "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]}}
        )
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


class QuickshareTests(unittest.TestCase):
    def test_guided_flow_publishes_only_chosen_turns_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session, notes = root / "s.jsonl", root / "notes.md"
            _session(session)
            notes.write_text("# shared notes")
            answers = iter(["1", str(notes), "", "y"])
            output = io.StringIO()
            with mock.patch("sys.stdin") as stdin, mock.patch("builtins.input", lambda *_: next(answers)):
                stdin.isatty.return_value = True
                with redirect_stdout(output):
                    code = cli.quickshare(
                        session, "claude-code-session", "Alice", root / "data", False, None
                    )
            self.assertEqual(code, 0)
            text = output.getvalue()
            self.assertIn("Invitation code", text)
            database = PrismDatabase(root / "data" / "prism.db")
            snapshot = PublicationService(database).list()[0]
            content = PublicationService(database).get(snapshot.snapshot_id).content
            self.assertEqual(len(content.messages), 2)
            self.assertEqual([r.display_name for r in content.resources], ["notes.md"])
            self.assertNotIn("QSECRET", content.model_dump_json())
            invitation = SharingService(database).list_invitations()[0]
            self.assertEqual(invitation.recipient_hint, "Alice")
            self.assertTrue(invitation.require_approval)

    def test_declining_publication_shares_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "s.jsonl"
            _session(session)
            answers = iter(["all", "", "n"])
            with mock.patch("sys.stdin") as stdin, mock.patch("builtins.input", lambda *_: next(answers)):
                stdin.isatty.return_value = True
                with redirect_stdout(io.StringIO()):
                    code = cli.quickshare(session, "claude-code-session", None, root / "data", False, None)
            self.assertEqual(code, 1)
            self.assertEqual(PublicationService(PrismDatabase(root / "data" / "prism.db")).list(), ())

    def test_chatgpt_link_flow_publishes_chosen_turns_and_warns_to_delete_the_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = PrismDatabase(root / "data" / "prism.db")
            services = (
                DurableSharedLinkCaptureService(
                    ChatGPTSharedLinkAdapter(fetcher=StaticFetcher(synthetic_shared_page())),
                    CaptureNormalizer(),
                    database,
                ),
                DraftService(database),
            )
            # url is read visibly -> input; then: store? y, turns, no files, publish y
            answers = iter([SHARE_URL, "y", "1", "", "y"])
            output = io.StringIO()
            with (
                mock.patch("sys.stdin") as stdin,
                mock.patch("prism.cli._shared_link_services", return_value=services),
                mock.patch("builtins.input", lambda *_: next(answers)),
                redirect_stdout(output),
            ):
                stdin.isatty.return_value = True
                code = cli.quickshare(
                    None, "claude-code-session", "Alice", root / "data", False, None,
                    chatgpt_link=True,
                )
            self.assertEqual(code, 0)
            text = output.getvalue()
            self.assertIn("Invitation code", text)
            self.assertIn("Delete it in ChatGPT", text)
            snapshot = PublicationService(database).list()[0]
            content = PublicationService(database).get(snapshot.snapshot_id).content
            self.assertEqual(len(content.messages), 2)
            self.assertEqual(SharingService(database).list_invitations()[0].recipient_hint, "Alice")

    def test_declining_the_chatgpt_capture_stores_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = PrismDatabase(root / "data" / "prism.db")
            services = (
                DurableSharedLinkCaptureService(
                    ChatGPTSharedLinkAdapter(fetcher=StaticFetcher(synthetic_shared_page())),
                    CaptureNormalizer(),
                    database,
                ),
                DraftService(database),
            )
            answers = iter([SHARE_URL, "n"])
            with (
                mock.patch("sys.stdin") as stdin,
                mock.patch("prism.cli._shared_link_services", return_value=services),
                mock.patch("builtins.input", lambda *_: next(answers)),
                redirect_stdout(io.StringIO()),
            ):
                stdin.isatty.return_value = True
                with self.assertRaises(Exception):
                    cli.quickshare(None, "x", None, root / "data", False, None, chatgpt_link=True)
            self.assertEqual(DraftService(database).list(), ())

    def test_grants_watch_approves_and_denies_pending_recipients(self) -> None:
        import threading
        from datetime import datetime, timedelta, timezone
        from prism.services.recipient_access import RecipientAccessService
        from tests.services.phase5_helpers import publish_prepared

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, _, published = publish_prepared(root)
            sharing = SharingService(database)
            share = sharing.create_share(published.snapshot_id, "Watch")
            access = RecipientAccessService(database)
            grants = []
            for name in ("Alice", "Mallory"):
                future = lambda h: (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat().replace("+00:00", "Z")
                invitation = sharing.create_invitation(
                    share.share.share_id, version=None, expires_at=future(2),
                    grant_expires_at=future(24), recipient_hint="Alice",
                )
                grants.append(access.redeem_invitation(invitation.invitation_token, name))
            lines = []
            stop = threading.Event()
            answered = []

            def ask(_prompt):
                reply = "y" if "'Alice' redeemed" in lines[-1] else "n"
                answered.append(reply)
                if len(answered) == 2:
                    stop.set()
                return reply

            code = cli.grants_watch(root, 0.01, stop, ask, lines.append)
            self.assertEqual(code, 0)
            by_id = {g.grant_id: g for g in sharing.list_grants()}
            statuses = sorted(g.approval.value for g in by_id.values())
            self.assertEqual(statuses, ["approved", "denied"])
            self.assertTrue(any("you made for 'Alice'" in line for line in lines))
            self.assertEqual(len(access.get_manifest(grants[0].grant_id).messages), 2)  # Alice
            with self.assertRaises(Exception):
                access.get_manifest(grants[1].grant_id)  # Mallory was denied

    def test_refuses_to_run_non_interactively(self) -> None:
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaises(Exception):
                cli.quickshare(None, "claude-code-session", None, None, False, None)


if __name__ == "__main__":
    unittest.main()
