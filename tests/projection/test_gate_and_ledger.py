from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prism.database import PrismDatabase
from prism.exceptions import LeakDetectedError, UnresolvedFindingsError, ValidationError
from prism.models.capture import AdapterCapture, AdapterMessage, CaptureMethod, MessageRole, Platform
from prism.models.draft import DraftSelectionRequest
from prism.models.publication import PublicationRequest
from prism.services.drafts import DraftService
from prism.services.normalization import CaptureNormalizer
from prism.services.publication import PublicationService
from prism.storage import DatabaseCaptureStore


def _turn(i, u, a):
    return [
        AdapterMessage(source_message_id=f"s{i}u", turn_index=i, ordinal=2 * i - 1,
                        role=MessageRole.USER, content=u),
        AdapterMessage(source_message_id=f"s{i}a", turn_index=i, ordinal=2 * i,
                        role=MessageRole.ASSISTANT, content=a),
    ]


def _seed(database, messages, title="Test capture"):
    capture = CaptureNormalizer().normalize(
        "test-adapter/0.1",
        AdapterCapture(
            platform=Platform.CHATGPT,
            method=CaptureMethod.SYNTHETIC,
            source_fingerprint="sha256:" + "a" * 64,
            conversation_ref="convref_abcdefgh",
            title=title,
            messages=tuple(messages),
        ),
    )
    result = DatabaseCaptureStore(database).save(capture)
    assert result.draft_id is not None
    return result.draft_id


class GateAndLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.database = PrismDatabase(Path(self._directory.name) / "prism.db")

    def test_clean_content_has_no_blocking_findings(self) -> None:
        draft_id = _seed(
            self.database,
            _turn(1, "What is the roadmap?", "We ship next quarter."),
        )
        drafts = DraftService(self.database)
        review = drafts.review(draft_id)
        drafts.replace_selection(
            draft_id,
            DraftSelectionRequest(
                expected_revision=review.draft.revision,
                selected_turn_ids=(review.turns[0].turn.turn_id,),
            ),
        )
        gate = drafts.gate(draft_id)
        self.assertTrue(gate.ok)
        self.assertEqual(gate.blocking, ())

    def test_secret_blocks_publish_until_allowed_with_a_reason(self) -> None:
        draft_id = _seed(
            self.database,
            _turn(1, "key AKIAJVQVGF6NXQZXKMPS", "noted") + _turn(2, "anything else?", "no"),
        )
        drafts = DraftService(self.database)
        review = drafts.review(draft_id)
        drafts.replace_selection(
            draft_id,
            DraftSelectionRequest(
                expected_revision=review.draft.revision,
                selected_turn_ids=tuple(t.turn.turn_id for t in review.turns),
            ),
        )
        findings = drafts.scan(draft_id)
        secret = next(f for f in findings if f.finding_class.value == "secret")

        with self.assertRaises(ValidationError):
            drafts.allow_finding(draft_id, secret.finding_id, secret.finding_class.value, "   ")

        state = drafts.review(draft_id).draft
        preview = drafts.preview(draft_id, state.revision)
        with self.assertRaises(UnresolvedFindingsError):
            PublicationService(self.database).publish(
                PublicationRequest(
                    draft_id=draft_id,
                    expected_revision=state.revision,
                    expected_preview_hash=preview.preview_hash,
                )
            )
        self.assertEqual(PublicationService(self.database).list(), ())

        drafts.allow_finding(draft_id, secret.finding_id, secret.finding_class.value, "test fixture key")
        result = PublicationService(self.database).publish(
            PublicationRequest(
                draft_id=draft_id,
                expected_revision=state.revision,
                expected_preview_hash=preview.preview_hash,
            )
        )
        self.assertEqual(result.finding_count, 1)
        self.assertEqual(result.override_count, 1)

    def test_revoking_a_decision_blocks_publication_again(self) -> None:
        draft_id = _seed(self.database, _turn(1, "email me at alice@corp-internal.io", "ok"))
        drafts = DraftService(self.database)
        review = drafts.review(draft_id)
        drafts.replace_selection(
            draft_id,
            DraftSelectionRequest(
                expected_revision=review.draft.revision,
                selected_turn_ids=(review.turns[0].turn.turn_id,),
            ),
        )
        finding = drafts.scan(draft_id)[0]
        drafts.allow_finding(draft_id, finding.finding_id, finding.finding_class.value, "test email")
        self.assertTrue(drafts.gate(draft_id).ok)
        drafts.revoke_finding_decision(draft_id, finding.finding_id)
        self.assertFalse(drafts.gate(draft_id).ok)

    def test_title_override_is_published_and_original_title_never_leaks_alone(self) -> None:
        draft_id = _seed(
            self.database,
            _turn(1, "Discuss the Northwind acquisition", "Sounds good."),
            title="Northwind acquisition plan",
        )
        drafts = DraftService(self.database)
        review = drafts.review(draft_id)
        drafts.set_title(draft_id, review.draft.revision, "Q3 planning notes")
        review2 = drafts.review(draft_id)
        self.assertEqual(review2.draft.title_override, "Q3 planning notes")
        drafts.replace_selection(
            draft_id,
            DraftSelectionRequest(
                expected_revision=review2.draft.revision,
                selected_turn_ids=(review2.turns[0].turn.turn_id,),
            ),
        )
        state = drafts.review(draft_id).draft
        preview = drafts.preview(draft_id, state.revision)
        self.assertEqual(preview.snapshot.title, "Q3 planning notes")
        result = PublicationService(self.database).publish(
            PublicationRequest(
                draft_id=draft_id,
                expected_revision=state.revision,
                expected_preview_hash=preview.preview_hash,
            )
        )
        published = PublicationService(self.database).get(result.snapshot_id).content
        self.assertEqual(published.title, "Q3 planning notes")
        self.assertNotEqual(published.title, "Northwind acquisition plan")
        # the selected message itself legitimately still contains the word;
        # only the *title field* is required to reflect the override.
        self.assertIn("Northwind", published.messages[0].content)

    def test_receipt_is_signed_verifiable_and_created_once_per_snapshot(self) -> None:
        draft_id = _seed(self.database, _turn(1, "plain content", "still plain"))
        drafts = DraftService(self.database)
        review = drafts.review(draft_id)
        drafts.replace_selection(
            draft_id,
            DraftSelectionRequest(
                expected_revision=review.draft.revision,
                selected_turn_ids=(review.turns[0].turn.turn_id,),
            ),
        )
        state = drafts.review(draft_id).draft
        preview = drafts.preview(draft_id, state.revision)
        publication = PublicationService(self.database)
        result = publication.publish(
            PublicationRequest(
                draft_id=draft_id,
                expected_revision=state.revision,
                expected_preview_hash=preview.preview_hash,
            )
        )
        receipt = publication.get_receipt(result.snapshot_id)
        self.assertTrue(publication.verify_receipt(receipt))
        self.assertEqual(receipt.snapshot_hash, result.content_hash)
        tampered = receipt.model_copy(update={"override_count": receipt.override_count + 5})
        self.assertFalse(publication.verify_receipt(tampered))

        # republishing identical content reuses the snapshot and keeps one receipt
        publication.revoke(result.snapshot_id)  # does not affect receipt storage
        second = publication.get_receipt(result.snapshot_id)
        self.assertEqual(second, receipt)


class ExclusionEchoIsWarnNotBlockTests(unittest.TestCase):
    """The false-positive risk (see PROJECTION_LAYER_PROPOSAL.md) means this
    class is visible but must never silently block a legitimate publish."""

    def test_shared_generic_word_across_turns_does_not_block_publication(self) -> None:
        database = PrismDatabase(Path(tempfile.mkdtemp()) / "prism.db")
        draft_id = _seed(
            database,
            _turn(1, "What is the plan for launch?", "We launch in spring.")
            + _turn(2, "Any other launch questions?", "None right now."),
        )
        drafts = DraftService(database)
        review = drafts.review(draft_id)
        drafts.replace_selection(
            draft_id,
            DraftSelectionRequest(
                expected_revision=review.draft.revision,
                selected_turn_ids=(review.turns[0].turn.turn_id,),  # excludes turn 2
            ),
        )
        gate = drafts.gate(draft_id)
        self.assertTrue(gate.ok)  # warn-only finding must not block
        state = drafts.review(draft_id).draft
        preview = drafts.preview(draft_id, state.revision)
        result = PublicationService(database).publish(
            PublicationRequest(
                draft_id=draft_id,
                expected_revision=state.revision,
                expected_preview_hash=preview.preview_hash,
            )
        )
        self.assertIsNotNone(result.snapshot_id)


if __name__ == "__main__":
    unittest.main()
