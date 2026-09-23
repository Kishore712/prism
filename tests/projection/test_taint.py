from __future__ import annotations

import unittest

from prism.models.capture import (
    AdapterCapture,
    AdapterMessage,
    CaptureMethod,
    MessageRole,
    Platform,
)
from prism.projection.detectors import FindingClass, FindingSeverity
from prism.projection.provenance import ToolReadEventRecord
from prism.projection.taint import provenance_derived_findings
from prism.services.normalization import CaptureNormalizer


def _turn(i: int, u: str, a: str) -> list[AdapterMessage]:
    return [
        AdapterMessage(source_message_id=f"s{i}u", turn_index=i, ordinal=2 * i - 1,
                        role=MessageRole.USER, content=u),
        AdapterMessage(source_message_id=f"s{i}a", turn_index=i, ordinal=2 * i,
                        role=MessageRole.ASSISTANT, content=a),
    ]


def _capture(turn_count: int = 4):
    messages: list[AdapterMessage] = []
    for i in range(1, turn_count + 1):
        messages += _turn(i, f"question {i}", f"answer {i}")
    return CaptureNormalizer().normalize(
        "test-adapter/0.1",
        AdapterCapture(
            platform=Platform.CLAUDE,
            method=CaptureMethod.LOCAL_SESSION,
            source_fingerprint="sha256:" + "a" * 64,
            conversation_ref="convref_abcdefgh",
            title="Test capture",
            messages=tuple(messages),
        ),
    )


def _event(event_id: str, turn_index: int, capture_id: str) -> ToolReadEventRecord:
    return ToolReadEventRecord(
        event_id=event_id,
        capture_id=capture_id,
        turn_index=turn_index,
        tool_name="Read",
        resource_label="/tmp/whatever.txt",
        created_at="2026-09-22T00:00:00Z",
    )


class ProvenanceDerivedFindingsTests(unittest.TestCase):
    def test_no_tainted_events_means_no_findings(self) -> None:
        capture = _capture()
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (_event("tce_1", 2, capture.capture_id),)
        findings = provenance_derived_findings(capture, turn_ids, events, frozenset())
        self.assertEqual(findings, ())

    def test_flags_the_tainted_turn_and_everything_after_it_only(self) -> None:
        capture = _capture(turn_count=4)
        turn_ids = list(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (_event("tce_1", 2, capture.capture_id),)  # tainted read happened at turn 2
        findings = provenance_derived_findings(
            capture, tuple(turn_ids), events, frozenset({"tce_1"})
        )
        flagged_turn_ids = {f.atom_ref for f in findings}
        self.assertEqual(flagged_turn_ids, {turn_ids[1], turn_ids[2], turn_ids[3]})  # turns 2,3,4
        self.assertNotIn(turn_ids[0], flagged_turn_ids)  # turn 1 predates the read
        for finding in findings:
            self.assertEqual(finding.finding_class, FindingClass.PROVENANCE_DERIVED)
            self.assertEqual(finding.severity, FindingSeverity.BLOCK)

    def test_only_currently_included_turns_are_flagged(self) -> None:
        capture = _capture(turn_count=4)
        turn_ids = list(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (_event("tce_1", 2, capture.capture_id),)
        # Owner has only included turn 4 (turns 2-3 already excluded via selection).
        findings = provenance_derived_findings(
            capture, (turn_ids[3],), events, frozenset({"tce_1"})
        )
        self.assertEqual([f.atom_ref for f in findings], [turn_ids[3]])

    def test_the_tainted_turn_itself_is_flagged_not_only_later_ones(self) -> None:
        capture = _capture(turn_count=2)
        turn_ids = list(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (_event("tce_1", 1, capture.capture_id),)
        findings = provenance_derived_findings(
            capture, tuple(turn_ids), events, frozenset({"tce_1"})
        )
        self.assertEqual({f.atom_ref for f in findings}, {turn_ids[0], turn_ids[1]})

    def test_multiple_tainted_events_each_contribute_independently(self) -> None:
        capture = _capture(turn_count=4)
        turn_ids = list(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (
            _event("tce_early", 1, capture.capture_id),
            _event("tce_late", 3, capture.capture_id),
        )
        findings = provenance_derived_findings(
            capture, tuple(turn_ids), events, frozenset({"tce_early", "tce_late"})
        )
        # turn 4 is covered by both events -> two distinct, separately resolvable findings
        turn4_findings = [f for f in findings if f.atom_ref == turn_ids[3]]
        self.assertEqual(len(turn4_findings), 2)
        self.assertNotEqual(turn4_findings[0].finding_id, turn4_findings[1].finding_id)
        # turn 1 is only covered by the early event
        turn1_findings = [f for f in findings if f.atom_ref == turn_ids[0]]
        self.assertEqual(len(turn1_findings), 1)

    def test_untainting_an_event_removes_its_findings(self) -> None:
        capture = _capture(turn_count=3)
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (_event("tce_1", 1, capture.capture_id),)
        with_taint = provenance_derived_findings(capture, turn_ids, events, frozenset({"tce_1"}))
        self.assertTrue(with_taint)
        without_taint = provenance_derived_findings(capture, turn_ids, events, frozenset())
        self.assertEqual(without_taint, ())

    def test_finding_id_is_stable_across_recomputation(self) -> None:
        capture = _capture(turn_count=2)
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (_event("tce_1", 1, capture.capture_id),)
        first = provenance_derived_findings(capture, turn_ids, events, frozenset({"tce_1"}))
        second = provenance_derived_findings(capture, turn_ids, events, frozenset({"tce_1"}))
        self.assertEqual(first, second)

    def test_events_for_a_dropped_or_unknown_turn_id_are_ignored_safely(self) -> None:
        capture = _capture(turn_count=2)
        events = (_event("tce_1", 1, capture.capture_id),)
        findings = provenance_derived_findings(
            capture, ("turn_does_not_exist_00000",), events, frozenset({"tce_1"})
        )
        self.assertEqual(findings, ())

    def test_excerpt_and_detail_never_repeat_the_tainted_resource_label(self) -> None:
        capture = _capture(turn_count=2)
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        events = (_event("tce_1", 1, capture.capture_id),)
        findings = provenance_derived_findings(capture, turn_ids, events, frozenset({"tce_1"}))
        for finding in findings:
            self.assertNotIn("/tmp/whatever.txt", finding.excerpt)
            self.assertNotIn("/tmp/whatever.txt", finding.detail)


if __name__ == "__main__":
    unittest.main()
