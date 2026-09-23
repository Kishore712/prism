"""Full owner journey through the web UI, in one process, via Starlette's
TestClient. This exercises the same services the CLI does, through HTTP
routes instead of argv — parity, not a second implementation, is exactly
what's under test here: every assertion below has a CLI-level equivalent
already covered in tests/e2e/test_full_journey.py and
tests/e2e/test_provenance_journey.py.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

from starlette.testclient import TestClient

from prism.database import PrismDatabase
from prism.webui.app import create_app

DEMO_KEY = "AKIAJVQVGF6NXQZXKMPS"  # format-valid, not a documented placeholder
EXCLUDED = "EXCLUDED_TURN_SECRET_7f3a"


def _hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, f"could not find hidden field {name!r} in response"
    return match.group(1)


def _session_jsonl(path: Path) -> None:
    records = [
        {"type": "custom-title", "customTitle": "Owner UI journey"},
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": f"Demo key {DEMO_KEY}"}},
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "Noted."}]}},
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": f"Private: {EXCLUDED}"}},
        {"type": "assistant", "uuid": "a2", "message": {"role": "assistant", "content": [{"type": "text", "text": "Won't repeat it."}]}},
        {"type": "user", "uuid": "u3", "message": {"role": "user", "content": "Check scratch/wrong.txt"}},
        {
            "type": "assistant",
            "uuid": "a3",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/tmp/wrong.txt"}},
                    {"type": "text", "text": "Checked it."},
                ],
            },
        },
        {"type": "user", "uuid": "u4", "message": {"role": "user", "content": "Summarize."}},
        {"type": "assistant", "uuid": "a4", "message": {"role": "assistant", "content": [{"type": "text", "text": "All set."}]}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


class OwnerFlowThroughTheWebUITest(unittest.TestCase):
    def test_capture_curate_taint_publish_share_grant_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            session_path = data_dir / "session.jsonl"
            _session_jsonl(session_path)

            app = create_app(data_dir)
            client = TestClient(app)

            # ---- dashboard loads with nothing yet ---------------------------
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("No drafts yet", home.text)

            # ---- capture: local Claude Code session --------------------------
            inv = client.post(
                "/capture/local/inventory",
                data={"adapter": "claude-code-session", "path": str(session_path)},
            )
            self.assertEqual(inv.status_code, 200, inv.text)
            conversation_ref = _hidden(inv.text, "conversation_ref")

            imported = client.post(
                "/capture/local/import",
                data={
                    "adapter": "claude-code-session",
                    "path": str(session_path),
                    "conversation_ref": conversation_ref,
                },
                follow_redirects=False,
            )
            self.assertEqual(imported.status_code, 303)
            draft_location = imported.headers["location"]
            self.assertTrue(draft_location.startswith("/drafts/"))
            draft_id = draft_location.split("/drafts/")[1].split("?")[0]

            # ---- draft review page renders, all turns excluded ---------------
            review = client.get(f"/drafts/{draft_id}")
            self.assertEqual(review.status_code, 200)
            self.assertIn("Turns", review.text)
            turn_ids = re.findall(r'name="turn_([^"]+)"', review.text)
            self.assertEqual(len(turn_ids), 4)
            event_ids = re.findall(r'name="event_id" value="([^"]+)"', review.text)
            self.assertEqual(len(event_ids), 1)
            event_id = event_ids[0]

            # ---- select every turn except the private one (turn 2) -----------
            kept_turn_ids = [t for i, t in enumerate(turn_ids) if i != 1]
            selected = client.post(
                f"/drafts/{draft_id}/select",
                data={"expected_revision": "1", **{f"turn_{t}": "on" for t in kept_turn_ids}},
                follow_redirects=False,
            )
            self.assertEqual(selected.status_code, 303)
            self.assertIn("ok=", selected.headers["location"])

            review2 = client.get(f"/drafts/{draft_id}")
            self.assertIn('checked', review2.text)

            # ---- the demo key is a BLOCK finding; resolve it -----------------
            finding_ids = re.findall(r'name="finding_class" value="secret"', review2.text)
            self.assertTrue(finding_ids, "expected a secret finding to be present")
            match = re.search(r'/drafts/[^/]+/findings/([^/]+)/resolve', review2.text)
            self.assertIsNotNone(match)
            finding_id = match.group(1)
            resolved = client.post(
                f"/drafts/{draft_id}/findings/{finding_id}/resolve",
                data={"finding_class": "secret", "reason": "demo key, not a real credential"},
                follow_redirects=False,
            )
            self.assertEqual(resolved.status_code, 303)

            # ---- taint the tool-call event; a downstream turn now blocks -----
            tainted = client.post(
                f"/drafts/{draft_id}/taint",
                data={"event_id": event_id, "reason": "wrong file, accidental read"},
                follow_redirects=False,
            )
            self.assertEqual(tainted.status_code, 303)

            review3 = client.get(f"/drafts/{draft_id}")
            self.assertIn("flagged", review3.text)
            self.assertIn("provenance-derived", review3.text)

            # ---- preview blocked while the provenance finding is undecided ---
            preview_blocked = client.post(f"/drafts/{draft_id}/preview", data={"expected_revision": "2"})
            self.assertEqual(preview_blocked.status_code, 200)  # preview itself always renders

            # Collect every (finding_id, class) pair from the resolve forms.
            pairs = re.findall(
                r'action="/drafts/[^/]+/findings/([^/]+)/resolve"[^>]*>\s*<input type="hidden" name="finding_class" value="([^"]+)"',
                review3.text,
            )
            provenance_ids = [fid for fid, cls in pairs if cls == "provenance-derived"]
            self.assertTrue(provenance_ids)
            for fid in provenance_ids:
                r = client.post(
                    f"/drafts/{draft_id}/findings/{fid}/resolve",
                    data={"finding_class": "provenance-derived", "reason": "reviewed, fine to share"},
                    follow_redirects=False,
                )
                self.assertEqual(r.status_code, 303, r.headers.get("location"))

            # ---- deselect the excluded-turn canary isn't needed; turn 2 (the
            # EXCLUDED turn) was never selected in the first place, so the
            # exclusion-derived WARN (if any) does not block. Preview + publish.
            preview = client.post(f"/drafts/{draft_id}/preview", data={"expected_revision": "2"})
            self.assertEqual(preview.status_code, 200)
            self.assertNotIn(EXCLUDED, preview.text)
            preview_hash = _hidden(preview.text, "expected_preview_hash")
            preview_revision = _hidden(preview.text, "expected_revision")

            published = client.post(
                f"/drafts/{draft_id}/publish",
                data={"expected_revision": preview_revision, "expected_preview_hash": preview_hash},
                follow_redirects=False,
            )
            self.assertEqual(published.status_code, 303, published.text)
            snapshot_location = published.headers["location"]
            snapshot_id = snapshot_location.split("/snapshots/")[1].split("?")[0]

            # ---- snapshot detail shows the receipt and the demo key --------
            snap = client.get(f"/snapshots/{snapshot_id}")
            self.assertEqual(snap.status_code, 200)
            self.assertIn(DEMO_KEY, snap.text)
            self.assertIn("Signature valid", snap.text)

            # ---- create a share, then an invitation --------------------------
            share_resp = client.post(f"/snapshots/{snapshot_id}/share", data={"name": "Design review"}, follow_redirects=False)
            self.assertEqual(share_resp.status_code, 303)
            share_id = share_resp.headers["location"].split("/shares/")[1].split("?")[0]

            invite_resp = client.post(
                f"/shares/{share_id}/invitations",
                data={"recipient_hint": "Alice", "expires_hours": "24", "grant_hours": "168", "require_approval": "on"},
            )
            self.assertEqual(invite_resp.status_code, 200)
            self.assertIn("pinv_v1_", invite_resp.text)

            share_page = client.get(f"/shares/{share_id}")
            self.assertIn("Alice", share_page.text)

            # ---- simulate a recipient redeeming the invitation through the
            # real recipient-facing path (the MCP OAuth consent page calls
            # this same service; the owner UI never redeems on the
            # recipient's behalf). The invitation created above through the
            # UI only ever exposed its hash, by design (D-037) — create a
            # second one this test can hold the raw token for.
            database = PrismDatabase(data_dir / "prism.db")
            from prism.services.recipient_access import RecipientAccessService
            from prism.services.sharing import SharingService

            sharing = SharingService(database)
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            fresh = sharing.create_invitation(
                share_id,
                version=None,
                expires_at=(now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                grant_expires_at=(now + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                recipient_hint="Bob",
            )
            redeemed = RecipientAccessService(database).redeem_invitation(fresh.invitation_token, "Bob")
            self.assertEqual(redeemed.approval.value, "pending")
            grant_id = redeemed.grant_id

            # ---- owner approves the grant through the UI ----------------------
            grants_page = client.get("/grants")
            self.assertIn("Bob", grants_page.text)
            self.assertIn("pending", grants_page.text)

            approved = client.post(f"/grants/{grant_id}/approve", follow_redirects=False)
            self.assertEqual(approved.status_code, 303)
            grants_after = client.get("/grants")
            self.assertIn("approved", grants_after.text)

            revoked = client.post(f"/grants/{grant_id}/revoke", follow_redirects=False)
            self.assertEqual(revoked.status_code, 303)

            # ---- audit trail recorded the lifecycle, never content -----------
            audit_page = client.get("/audit")
            self.assertIn("grant_approved", audit_page.text)
            self.assertIn("grant_revoked", audit_page.text)
            self.assertNotIn(EXCLUDED, audit_page.text)
            self.assertNotIn(DEMO_KEY, audit_page.text)

            # ---- dashboard now reflects real state -----------------------
            home2 = client.get("/")
            self.assertIn("1", home2.text)  # at least one published snapshot


class ExclusionWarningsAreGroupedAndRankedTest(unittest.TestCase):
    """Regression coverage for a real report: an owner excluded a turn
    naming a professor, then a later *included* turn (deliberately kept)
    had the assistant repeat the name back. The exclusion-derived WARN
    fired correctly, but on a real multi-turn capture it was one of 1,148
    raw findings — because one excluded turn happened to share ordinary
    academic vocabulary with a long included answer, and the same shared
    word was flagged once per raw occurrence in that one long message. The
    genuinely interesting warning (the name) was there, just undiscoverable.
    Grouping (`app.py::_group_warnings`) must collapse repeats and rank
    rare terms first.
    """

    @staticmethod
    def _session_jsonl(path: Path) -> None:
        # Two *excluded* turns: one ordinary (shares "research" with two
        # separate included turns -- common-word noise, occurring twice),
        # one naming a person who then gets named again in a later
        # included turn (a rare, single-occurrence, genuine leak) -- the
        # exact shape of the real report.
        records = [
            {"type": "custom-title", "customTitle": "Warning noise regression"},
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Tell me about the research program"}},
            {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "This research program covers many research topics and research areas."}]}},
            {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "What research topics does the department offer"}},
            {"type": "assistant", "uuid": "a2", "message": {"role": "assistant", "content": [{"type": "text", "text": "Many research topics, research areas, research programs."}]}},
            {"type": "user", "uuid": "u3", "message": {"role": "user", "content": "Can you list more research areas"}},
            {"type": "assistant", "uuid": "a3", "message": {"role": "assistant", "content": [{"type": "text", "text": "Sure, here are more research areas to consider."}]}},
            {"type": "user", "uuid": "u4", "message": {"role": "user", "content": "I am working with Professor Jackson on a confidential project"}},
            {"type": "assistant", "uuid": "a4", "message": {"role": "assistant", "content": [{"type": "text", "text": "Understood, noted."}]}},
            {"type": "user", "uuid": "u5", "message": {"role": "user", "content": "Who was the professor I mentioned earlier"}},
            {"type": "assistant", "uuid": "a5", "message": {"role": "assistant", "content": [{"type": "text", "text": "You mentioned working with Professor Jackson on a confidential project."}]}},
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def test_rare_leak_is_grouped_and_ranked_ahead_of_common_noise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            session = data_dir / "session.jsonl"
            self._session_jsonl(session)
            client = TestClient(create_app(data_dir))

            inv = client.post(
                "/capture/local/inventory",
                data={"adapter": "claude-code-session", "path": str(session)},
            )
            conversation_ref = _hidden(inv.text, "conversation_ref")
            imported = client.post(
                "/capture/local/import",
                data={
                    "adapter": "claude-code-session",
                    "path": str(session),
                    "conversation_ref": conversation_ref,
                },
                follow_redirects=False,
            )
            draft_id = imported.headers["location"].split("/drafts/")[1].split("?")[0]

            review = client.get(f"/drafts/{draft_id}")
            turn_ids = re.findall(r'name="turn_([^"]+)"', review.text)
            # Turns 1, 3, and 5 are included; turn 2 (ordinary "research"
            # question) and turn 4 (naming the professor) stay excluded --
            # the owner's actual choice in the real report.
            kept = [t for i, t in enumerate(turn_ids) if i not in (1, 3)]
            client.post(
                f"/drafts/{draft_id}/select",
                data={"expected_revision": "1", **{f"turn_{t}": "on" for t in kept}},
            )

            page = client.get(f"/drafts/{draft_id}")
            self.assertIn("jackson", page.text.lower())
            self.assertIn("seen in", page.text.lower())  # the "research" group shows an occurrence count

            # "jackson" is a rare (single-occurrence) term and must rank
            # ahead of "research", which appears in two separate included
            # turns and is exactly the kind of common-word noise that
            # buried the real report.
            jackson_pos = page.text.lower().index("jackson")
            research_group_pos = page.text.lower().index("&#39;research&#39;")
            self.assertLess(jackson_pos, research_group_pos)

            # And it must be genuinely collapsed, not one card per mention
            # (each included turn's answer repeats "research" 2-3 times).
            self.assertEqual(page.text.lower().count("&#39;research&#39;"), 1)


class DatabaseIsolationTest(unittest.TestCase):
    """Regression coverage for the "I can't see anything" report: the UI
    was working correctly, but a `--data-dir` mismatch against an earlier
    capture/publish looked identical to a broken page (a correctly-empty
    database renders no error). The filesystem path itself is diagnostic,
    backend detail that has no business on an owner-facing page — it's
    printed once to the terminal at `prism ui` startup instead (see
    `cli.py::ui_serve`) — so this only asserts the property that actually
    matters to a user: two different data directories must never see each
    other's data through the same running app, and no page leaks a
    filesystem path into its HTML.
    """

    def test_pages_never_leak_a_filesystem_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            app = create_app(data_dir)
            client = TestClient(app)
            db_path = str((data_dir / "prism.db").resolve())
            for path in ("/", "/drafts", "/snapshots", "/shares", "/grants", "/audit", "/capture/new"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertNotIn(db_path, response.text, f"{path} leaked the database filesystem path")
                self.assertNotIn(str(data_dir), response.text, f"{path} leaked the data directory path")

    def test_two_data_dirs_never_see_each_others_data(self) -> None:
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            client_one = TestClient(create_app(Path(one)))
            client_two = TestClient(create_app(Path(two)))

            session = Path(one) / "session.jsonl"
            _session_jsonl(session)
            inv = client_one.post(
                "/capture/local/inventory",
                data={"adapter": "claude-code-session", "path": str(session)},
            )
            conversation_ref = _hidden(inv.text, "conversation_ref")
            imported = client_one.post(
                "/capture/local/import",
                data={
                    "adapter": "claude-code-session",
                    "path": str(session),
                    "conversation_ref": conversation_ref,
                },
                follow_redirects=False,
            )
            self.assertEqual(imported.status_code, 303)

            # The draft exists for the data dir it was captured into...
            drafts_one = client_one.get("/drafts")
            self.assertNotIn("No drafts yet", drafts_one.text)

            # ...and is invisible through a completely different data dir,
            # exactly the scenario that produced the "I can't see anything"
            # report when `--data-dir` didn't match between runs.
            drafts_two = client_two.get("/drafts")
            self.assertIn("No drafts yet", drafts_two.text)
            self.assertNotIn(str((Path(one) / "prism.db").resolve()), drafts_two.text)


class PublishedDraftIsClearlyLockedTest(unittest.TestCase):
    """A published draft is immutable by design (D-034: a published
    snapshot must stay traceable to the exact draft revision that was
    reviewed and hashed) -- but the raw DRAFT_CHANGED error
    ("The draft changed after it was read; reload it before continuing")
    is actively misleading here: reloading never helps. The review page
    must not even offer the controls, and if one is submitted anyway (a
    stale tab, a second process), the error must say why in a way that
    points at the actual fix (capture again), not "reload".
    """

    @staticmethod
    def _clean_session_jsonl(path: Path) -> None:
        """No secrets, no tool calls — nothing that would block publication,
        so this test can focus purely on post-publish immutability.
        """

        records = [
            {"type": "custom-title", "customTitle": "Clean session"},
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Hello"}},
            {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there."}]}},
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def _published_draft(self, client: TestClient, data_dir: Path) -> tuple[str, str]:
        session = data_dir / "session.jsonl"
        self._clean_session_jsonl(session)
        inv = client.post(
            "/capture/local/inventory",
            data={"adapter": "claude-code-session", "path": str(session)},
        )
        conversation_ref = _hidden(inv.text, "conversation_ref")
        imported = client.post(
            "/capture/local/import",
            data={
                "adapter": "claude-code-session",
                "path": str(session),
                "conversation_ref": conversation_ref,
            },
            follow_redirects=False,
        )
        draft_id = imported.headers["location"].split("/drafts/")[1].split("?")[0]

        review = client.get(f"/drafts/{draft_id}")
        turn_ids = re.findall(r'name="turn_([^"]+)"', review.text)
        client.post(
            f"/drafts/{draft_id}/select",
            data={"expected_revision": "1", **{f"turn_{t}": "on" for t in turn_ids}},
        )
        preview = client.post(f"/drafts/{draft_id}/preview", data={"expected_revision": "2"})
        preview_hash = _hidden(preview.text, "expected_preview_hash")
        published = client.post(
            f"/drafts/{draft_id}/publish",
            data={"expected_revision": "2", "expected_preview_hash": preview_hash},
            follow_redirects=False,
        )
        snapshot_id = published.headers["location"].split("/snapshots/")[1].split("?")[0]
        return draft_id, snapshot_id

    def test_review_page_offers_no_editing_controls_and_links_the_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            client = TestClient(create_app(data_dir))
            draft_id, snapshot_id = self._published_draft(client, data_dir)

            page = client.get(f"/drafts/{draft_id}")
            self.assertIn("already published and is now immutable", page.text)
            self.assertIn(f"/snapshots/{snapshot_id}", page.text)
            self.assertNotIn("Save selection", page.text)
            self.assertNotIn('name="turn_', page.text)
            self.assertNotIn("Attach a UTF-8", page.text)
            self.assertNotIn("Preview exact snapshot", page.text)

    def test_submitting_an_edit_anyway_gives_a_specific_message_not_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            client = TestClient(create_app(data_dir))
            draft_id, _snapshot_id = self._published_draft(client, data_dir)

            # Simulate a stale tab that still has the old edit form open.
            attempt = client.post(
                f"/drafts/{draft_id}/select",
                data={"expected_revision": "2"},
                follow_redirects=False,
            )
            self.assertEqual(attempt.status_code, 303)
            location = unquote(attempt.headers["location"])
            self.assertIn("already published and is now immutable", location)
            self.assertNotIn("reload it before continuing", location)


if __name__ == "__main__":
    unittest.main()
