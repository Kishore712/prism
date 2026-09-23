"""Phase 9 service-layer tests: taint decisions gate publication.

Exercises the exact scenario that motivated Phase 9: an accidental tool
read happens mid-capture, a later included turn restates something from it
without repeating it verbatim (so no regex detector catches it), the owner
taints the read, and the draft becomes blocked at gate()/publish() until
the owner resolves it -- all through DraftService/PublicationService, not
the lower-level projection functions directly (those are covered in
tests/projection/test_taint.py and test_graph.py).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.exceptions import NotFoundError, UnresolvedFindingsError, ValidationError
from prism.models.capture import (
    AdapterCapture,
    AdapterMessage,
    CaptureMethod,
    MessageRole,
    Platform,
)
from prism.models.draft import DraftSelectionRequest
from prism.models.publication import PublicationRequest
from prism.projection.provenance import ToolReadEvent
from prism.services.capture import CaptureService
from prism.services.drafts import DraftService
from prism.services.normalization import CaptureNormalizer
from prism.services.publication import PublicationService
from prism.storage.database import DatabaseCaptureStore


def _turn(i: int, user: str, assistant: str) -> list[AdapterMessage]:
    return [
        AdapterMessage(
            source_message_id=f"s{i}u", turn_index=i, ordinal=2 * i - 1,
            role=MessageRole.USER, content=user,
        ),
        AdapterMessage(
            source_message_id=f"s{i}a", turn_index=i, ordinal=2 * i,
            role=MessageRole.ASSISTANT, content=assistant,
        ),
    ]


class ProvenanceTaintServiceTests(unittest.TestCase):
    def _setup(self, root: Path):
        """Capture a 3-turn session, with a Read tool event before turn 2."""

        db_path = root / "prism.db"
        database = PrismDatabase(db_path)
        store = DatabaseCaptureStore(database)

        messages = (
            _turn(1, "What retrieval approach should we use?", "Deterministic lexical retrieval.")
            + _turn(2, "Restating what the file said: rollout is codenamed BLUEHERON.", "Noted.")
            + _turn(3, "How do we notify recipients?", "Through the share invitation flow.")
        )
        capture = CaptureNormalizer().normalize(
            "test-adapter/0.1",
            AdapterCapture(
                platform=Platform.CLAUDE,
                method=CaptureMethod.LOCAL_SESSION,
                source_fingerprint="sha256:" + "a" * 64,
                conversation_ref="convref_abcdefgh",
                title="Accidental read scenario",
                messages=tuple(messages),
            ),
        )
        stored = store.save(capture)
        assert stored.draft_id is not None

        # The event: a Read on a file the owner did not mean to include,
        # which happened right before turn 2 (so turns 2 and 3 are tainted
        # once flagged, turn 1 is not).
        with PrismUnitOfWork(database) as unit:
            created = unit.tool_events.add_all(
                capture.capture_id,
                (ToolReadEvent(turn_index=2, tool_name="Read", resource_label="/tmp/wrong_file.txt"),),
            )
            unit.commit()
        event_id = created[0].event_id

        drafts = DraftService(database)
        publication = PublicationService(database)
        review = drafts.review(stored.draft_id)
        turn_ids = tuple(turn.turn.turn_id for turn in review.turns)
        return database, drafts, publication, stored.draft_id, event_id, turn_ids

    def test_provenance_lists_the_captured_tool_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, drafts, _, draft_id, event_id, _ = self._setup(Path(directory))
            events = drafts.provenance(draft_id)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_id, event_id)
            self.assertEqual(events[0].tool_name, "Read")
            self.assertEqual(events[0].resource_label, "/tmp/wrong_file.txt")

    def test_untainted_draft_selects_and_publishes_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, drafts, publication, draft_id, _event_id, turn_ids = self._setup(Path(directory))
            state = drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1, selected_turn_ids=turn_ids, selected_resource_ids=()
                ),
            )
            gate = drafts.gate(draft_id)
            self.assertTrue(gate.ok)
            preview = drafts.preview(draft_id, state.revision)
            result = publication.publish(
                PublicationRequest(
                    draft_id=draft_id,
                    expected_revision=state.revision,
                    expected_preview_hash=preview.preview_hash,
                )
            )
            self.assertTrue(result.snapshot_id)

    def test_tainting_the_read_blocks_gate_and_publish_for_downstream_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, drafts, publication, draft_id, event_id, turn_ids = self._setup(Path(directory))
            state = drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1, selected_turn_ids=turn_ids, selected_resource_ids=()
                ),
            )

            record = drafts.taint(draft_id, event_id, "Accidentally read the wrong file")
            self.assertEqual(record.event_id, event_id)
            self.assertEqual(record.draft_id, draft_id)

            gate = drafts.gate(draft_id)
            self.assertFalse(gate.ok)
            self.assertTrue(
                any(f.finding_class.value == "provenance-derived" for f in gate.blocking)
            )
            # turn 1 (before the read) must not be blocked; turns 2 and 3 must be.
            blocked_refs = {f.atom_ref for f in gate.blocking}
            self.assertNotIn(turn_ids[0], blocked_refs)
            self.assertIn(turn_ids[1], blocked_refs)
            self.assertIn(turn_ids[2], blocked_refs)

            preview = drafts.preview(draft_id, state.revision)
            with self.assertRaises(UnresolvedFindingsError):
                publication.publish(
                    PublicationRequest(
                        draft_id=draft_id,
                        expected_revision=state.revision,
                        expected_preview_hash=preview.preview_hash,
                    )
                )

    def test_deselecting_the_tainted_turns_unblocks_without_resolving_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, drafts, publication, draft_id, event_id, turn_ids = self._setup(Path(directory))
            drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1, selected_turn_ids=turn_ids, selected_resource_ids=()
                ),
            )
            drafts.taint(draft_id, event_id, "Wrong file")

            # Owner instead just drops the tainted turns from the selection.
            state = drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=2, selected_turn_ids=(turn_ids[0],), selected_resource_ids=()
                ),
            )
            gate = drafts.gate(draft_id)
            self.assertTrue(gate.ok)
            preview = drafts.preview(draft_id, state.revision)
            result = publication.publish(
                PublicationRequest(
                    draft_id=draft_id,
                    expected_revision=state.revision,
                    expected_preview_hash=preview.preview_hash,
                )
            )
            self.assertTrue(result.snapshot_id)

    def test_allowing_the_provenance_finding_unblocks_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, drafts, publication, draft_id, event_id, turn_ids = self._setup(Path(directory))
            state = drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1, selected_turn_ids=turn_ids, selected_resource_ids=()
                ),
            )
            drafts.taint(draft_id, event_id, "Wrong file, but content is actually fine")
            gate = drafts.gate(draft_id)
            self.assertFalse(gate.ok)
            for finding in gate.blocking:
                drafts.allow_finding(draft_id, finding.finding_id, finding.finding_class.value, "Reviewed, fine")

            gate_after = drafts.gate(draft_id)
            self.assertTrue(gate_after.ok)
            self.assertEqual(len(gate_after.overridden), len(gate.blocking))

            preview = drafts.preview(draft_id, state.revision)
            result = publication.publish(
                PublicationRequest(
                    draft_id=draft_id,
                    expected_revision=state.revision,
                    expected_preview_hash=preview.preview_hash,
                )
            )
            self.assertTrue(result.snapshot_id)

    def test_untaint_reverses_the_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, drafts, publication, draft_id, event_id, turn_ids = self._setup(Path(directory))
            drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1, selected_turn_ids=turn_ids, selected_resource_ids=()
                ),
            )
            drafts.taint(draft_id, event_id, "Wrong file")
            self.assertFalse(drafts.gate(draft_id).ok)

            drafts.untaint(draft_id, event_id)
            self.assertTrue(drafts.gate(draft_id).ok)

    def test_untaint_of_unknown_event_raises_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, drafts, _, draft_id, _event_id, _turn_ids = self._setup(Path(directory))
            with self.assertRaises(NotFoundError):
                drafts.untaint(draft_id, "tce_does_not_exist")

    def test_taint_requires_a_nonempty_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, drafts, _, draft_id, event_id, _turn_ids = self._setup(Path(directory))
            with self.assertRaises(ValidationError):
                drafts.taint(draft_id, event_id, "   ")

    def test_dependency_graph_surfaces_provenance_edges_for_downstream_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, drafts, publication, draft_id, event_id, turn_ids = self._setup(Path(directory))
            drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1, selected_turn_ids=turn_ids, selected_resource_ids=()
                ),
            )
            drafts.taint(draft_id, event_id, "Wrong file")

            edges = drafts.dependency_graph(draft_id)
            provenance_edges = [e for e in edges if e.kind == "provenance"]
            self.assertTrue(provenance_edges)
            self.assertTrue(all(e.target_ref == f"event:{event_id}" for e in provenance_edges))
            self.assertEqual({e.source_ref for e in provenance_edges}, {turn_ids[1], turn_ids[2]})


if __name__ == "__main__":
    unittest.main()
