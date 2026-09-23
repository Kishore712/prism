"""End-to-end CLI journey for Phase 9: accidental-read provenance taint.

The exact scenario that motivated this phase (owner's own words): "I can
accidentally add a file by mistake and the agent grinds on it but I don't
want that detail to propagate to the shared agent, [and] just disabling that
particular resource/message is not enough because that information could
have already leaked context into other parts of the thread."

This drives the real `prism` CLI as a subprocess end to end, the same way
`tests/e2e/test_full_journey.py` does for the base capture/publish flow:
capture a Claude Code session with a genuine ``Read`` tool call on a file the
owner did not mean to include, confirm the provenance timeline is captured
automatically, confirm a later turn that restates the read (without
repeating any regex-matchable secret) is NOT blocked before tainting, taint
the read, confirm downstream turns now block publication, then resolve it
two different ways (deselect vs. explicit allow) and confirm each unblocks
publication and produces the expected, exact recipient-visible content.

A published draft is immutable (by design -- see `repositories/drafts.py`
``_require_editable_revision``), so each branch below re-imports the same
session into a fresh capture/draft rather than mutating one already-
published draft; that mirrors how an owner would actually work (review,
publish; realize a problem; start a fresh share of the same underlying
conversation) more faithfully than fighting the immutability guarantee.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# A phrase that restates the accidental read's content without repeating
# anything a regex-based secret/PII detector would catch -- exactly the gap
# Phase 8's detectors alone cannot close.
RESTATED_DETAIL = "the rollout is codenamed BLUEHERON, per that file"


def _session_jsonl(path: Path) -> None:
    records = [
        {"type": "custom-title", "customTitle": "Accidental read scenario"},
        {
            "type": "user",
            "uuid": "u1",
            "message": {"role": "user", "content": "What should the retrieval approach be?"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Use deterministic lexical retrieval."}],
            },
        },
        {
            "type": "user",
            "uuid": "u2",
            "message": {"role": "user", "content": "Can you check scratch/wrong_file.txt for context?"},
        },
        {
            "type": "assistant",
            "uuid": "a2",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "/Users/owner/scratch/wrong_file.txt"},
                    },
                    {"type": "text", "text": "Checked it, noted internally."},
                ],
            },
        },
        {
            "type": "user",
            "uuid": "u3",
            "message": {"role": "user", "content": "Summarize what you found."},
        },
        {
            "type": "assistant",
            "uuid": "a3",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"Sure -- {RESTATED_DETAIL}."}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")


class ProvenanceJourneyTest(unittest.TestCase):
    def _run(self, *arguments: str, check: bool = True):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "prism", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if check and completed.returncode != 0:
            raise AssertionError(
                f"prism {' '.join(arguments)} failed ({completed.returncode})\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return completed

    def _json(self, *arguments: str, **kwargs):
        return json.loads(self._run(*arguments, "--json", **kwargs).stdout)

    def _fresh_draft(self, session: Path, data: list[str]) -> tuple[str, dict]:
        """Import a brand-new capture+draft from the same session file.

        Returns (draft_id, the single captured tool-read event as a dict).
        """

        inventory = self._json(
            "capture", "list", str(session), "--adapter", "claude-code-session", *data
        )
        ref = inventory["conversations"][0]["conversation_ref"]
        captured = self._json(
            "capture", "import", str(session), "--adapter", "claude-code-session",
            "--conversation-ref", ref, *data,
        )
        draft_id = captured["draft_id"]
        events = self._json("draft", "provenance", draft_id, *data)["events"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["tool_name"], "Read")
        self.assertIn("wrong_file.txt", event["resource_label"])
        self.assertFalse(event["tainted"])
        return draft_id, event

    def test_provenance_is_captured_and_unblocked_before_tainting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.jsonl"
            _session_jsonl(session)
            data = ["--data-dir", str(root / "owner")]

            draft_id, _event = self._fresh_draft(session, data)
            self._run("draft", "select", draft_id, "--turns", "1-3", *data)

            findings = self._json("draft", "findings", draft_id, *data)["findings"]
            self.assertFalse(any(f["finding_class"] == "provenance-derived" for f in findings))

            published = self._json(
                "draft", "publish", draft_id, "--yes", *data
            )
            snapshot = self._json("snapshot", "show", published["snapshot_id"], *data)
            # Confirm the restated detail really is in the published snapshot
            # pre-taint -- this is the exact leak the owner described: no
            # regex detector on earth flags "the rollout is codenamed
            # BLUEHERON, per that file" as a secret.
            self.assertIn(RESTATED_DETAIL, json.dumps(snapshot))

    def test_tainting_the_read_blocks_publication_of_downstream_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.jsonl"
            _session_jsonl(session)
            data = ["--data-dir", str(root / "owner")]

            draft_id, event = self._fresh_draft(session, data)
            self._run("draft", "select", draft_id, "--turns", "1-3", *data)

            taint_result = self._run(
                "draft", "taint", draft_id, event["event_id"],
                "--reason", "Accidentally pointed the agent at the wrong file", *data,
            )
            self.assertEqual(taint_result.returncode, 0, taint_result.stderr)

            provenance_after = self._json("draft", "provenance", draft_id, *data)["events"]
            self.assertTrue(provenance_after[0]["tainted"])

            # Dependency-graph suggestions point at the tainted event, for
            # turns 2 and 3 (at/after the read) -- turn 1 is untouched.
            graph = self._json("draft", "graph", draft_id, *data)["edges"]
            provenance_edges = [e for e in graph if e["kind"] == "provenance"]
            self.assertEqual(len(provenance_edges), 2)
            self.assertTrue(all(e["target_ref"] == f"event:{event['event_id']}" for e in provenance_edges))

            findings = self._json("draft", "findings", draft_id, *data)["findings"]
            provenance_findings = [f for f in findings if f["finding_class"] == "provenance-derived"]
            self.assertEqual(len(provenance_findings), 2)  # turns 2 and 3, not turn 1

            blocked = self._run("draft", "publish", draft_id, "--yes", *data, check=False)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("must be resolved first", blocked.stderr)

    def test_recovery_by_deselecting_the_tainted_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.jsonl"
            _session_jsonl(session)
            data = ["--data-dir", str(root / "owner")]

            draft_id, event = self._fresh_draft(session, data)
            self._run("draft", "select", draft_id, "--turns", "1-3", *data)
            self._run(
                "draft", "taint", draft_id, event["event_id"],
                "--reason", "Wrong file", *data,
            )
            blocked = self._run("draft", "publish", draft_id, "--yes", *data, check=False)
            self.assertNotEqual(blocked.returncode, 0)

            # Owner drops the turns at/after the tainted read instead of
            # reviewing them individually.
            self._run("draft", "select", draft_id, "--turns", "1", *data)
            findings = self._json("draft", "findings", draft_id, *data)["findings"]
            self.assertFalse(any(f["finding_class"] == "provenance-derived" for f in findings))

            published = self._json("draft", "publish", draft_id, "--yes", *data)
            snapshot = self._json("snapshot", "show", published["snapshot_id"], *data)
            self.assertNotIn(RESTATED_DETAIL, json.dumps(snapshot))
            self.assertEqual(published["message_count"], 2)  # turn 1 only

    def test_recovery_by_explicitly_resolving_each_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.jsonl"
            _session_jsonl(session)
            data = ["--data-dir", str(root / "owner")]

            draft_id, event = self._fresh_draft(session, data)
            self._run("draft", "select", draft_id, "--turns", "1-3", *data)
            self._run(
                "draft", "taint", draft_id, event["event_id"],
                "--reason", "Wrong file", *data,
            )

            findings = self._json("draft", "findings", draft_id, *data)["findings"]
            provenance_findings = [f for f in findings if f["finding_class"] == "provenance-derived"]
            self.assertTrue(all(not f["decided"] for f in provenance_findings))
            for finding in provenance_findings:
                self._run(
                    "draft", "resolve", draft_id, finding["finding_id"],
                    "--reason", "Reviewed; the restated detail is not actually sensitive", *data,
                )

            gate_findings = self._json("draft", "findings", draft_id, *data)["findings"]
            self.assertTrue(
                all(f["decided"] for f in gate_findings if f["finding_class"] == "provenance-derived")
            )

            published = self._json("draft", "publish", draft_id, "--yes", *data)
            snapshot = self._json("snapshot", "show", published["snapshot_id"], *data)
            # The owner explicitly reviewed and allowed it through, so it
            # correctly DOES reach the published snapshot.
            self.assertIn(RESTATED_DETAIL, json.dumps(snapshot))
            receipt = self._json("snapshot", "receipt", published["snapshot_id"], *data)
            self.assertEqual(receipt["override_count"], len(provenance_findings))

    def test_untaint_reverses_the_block_before_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.jsonl"
            _session_jsonl(session)
            data = ["--data-dir", str(root / "owner")]

            draft_id, event = self._fresh_draft(session, data)
            self._run("draft", "select", draft_id, "--turns", "1-3", *data)
            self._run(
                "draft", "taint", draft_id, event["event_id"],
                "--reason", "Wrong file, thought better of it", *data,
            )
            findings_while_tainted = self._json("draft", "findings", draft_id, *data)["findings"]
            self.assertTrue(
                any(f["finding_class"] == "provenance-derived" for f in findings_while_tainted)
            )
            blocked = self._run("draft", "publish", draft_id, "--yes", *data, check=False)
            self.assertNotEqual(blocked.returncode, 0)

            self._run("draft", "untaint", draft_id, event["event_id"], *data)
            provenance_final = self._json("draft", "provenance", draft_id, *data)["events"]
            self.assertFalse(provenance_final[0]["tainted"])
            findings_final = self._json("draft", "findings", draft_id, *data)["findings"]
            self.assertFalse(any(f["finding_class"] == "provenance-derived" for f in findings_final))

            published = self._json("draft", "publish", draft_id, "--yes", *data)
            snapshot = self._json("snapshot", "show", published["snapshot_id"], *data)
            self.assertIn(RESTATED_DETAIL, json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()
