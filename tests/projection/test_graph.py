from __future__ import annotations

import unittest

from prism.models.capture import (
    AdapterCapture,
    AdapterMessage,
    CaptureMethod,
    MessageRole,
    Platform,
)
from prism.projection.atoms import AtomSet, build_atoms
from prism.projection.detectors import Atom
from prism.projection.graph import (
    build_cue_phrase_edges,
    build_lexical_edges,
    build_provenance_edges,
    cascading_candidates,
)
from prism.projection.provenance import ToolReadEventRecord
from prism.services.normalization import CaptureNormalizer


def _turn(i, u, a):
    return [
        AdapterMessage(source_message_id=f"s{i}u", turn_index=i, ordinal=2 * i - 1,
                        role=MessageRole.USER, content=u),
        AdapterMessage(source_message_id=f"s{i}a", turn_index=i, ordinal=2 * i,
                        role=MessageRole.ASSISTANT, content=a),
    ]


def _capture(turns):
    messages = []
    for i, (u, a) in enumerate(turns, start=1):
        messages += _turn(i, u, a)
    return CaptureNormalizer().normalize(
        "test-adapter/0.1",
        AdapterCapture(
            platform=Platform.CLAUDE,
            method=CaptureMethod.LOCAL_SESSION,
            source_fingerprint="sha256:" + "a" * 64,
            conversation_ref="convref_abcdefgh",
            title="Test",
            messages=tuple(messages),
        ),
    )


class LexicalEdgeTests(unittest.TestCase):
    def test_edge_from_included_to_excluded_on_shared_term(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="we discussed BLUEHERON rollout")],
            excluded=[Atom(kind="message", ref="exc1", text="the codename is BLUEHERON project")],
        )
        edges = build_lexical_edges(atoms)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].source_ref, "inc1")
        self.assertEqual(edges[0].target_ref, "exc1")
        self.assertEqual(edges[0].kind, "lexical")
        self.assertEqual(edges[0].confidence, "medium")

    def test_no_excluded_atoms_means_no_edges(self) -> None:
        atoms = AtomSet(included=[Atom(kind="message", ref="inc1", text="anything at all")], excluded=[])
        self.assertEqual(build_lexical_edges(atoms), ())

    def test_no_shared_terms_means_no_edges(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="totally unrelated content")],
            excluded=[Atom(kind="message", ref="exc1", text="the codename is BLUEHERON project")],
        )
        self.assertEqual(build_lexical_edges(atoms), ())

    def test_multiple_excluded_sources_produce_multiple_edges(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="BLUEHERON was discussed twice")],
            excluded=[
                Atom(kind="message", ref="exc1", text="BLUEHERON codename one"),
                Atom(kind="message", ref="exc2", text="BLUEHERON codename two"),
            ],
        )
        edges = build_lexical_edges(atoms)
        self.assertEqual({e.target_ref for e in edges}, {"exc1", "exc2"})

    def test_punctuation_adjacent_terms_still_match(self) -> None:
        # Regression guard: an earlier version used a plain whitespace split,
        # which would miss "BLUEHERON," (trailing comma) matching "blueheron".
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="Regarding BLUEHERON, it's on track.")],
            excluded=[Atom(kind="message", ref="exc1", text="the codename is BLUEHERON project")],
        )
        edges = build_lexical_edges(atoms)
        self.assertEqual(len(edges), 1)


class CuePhraseEdgeTests(unittest.TestCase):
    def test_targets_the_nearest_preceding_excluded_message(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="As I mentioned earlier, it's ready.")],
            excluded=[
                Atom(kind="message", ref="exc1", text="first excluded"),
                Atom(kind="message", ref="exc2", text="second excluded"),
            ],
        )
        edges = build_cue_phrase_edges(atoms)
        self.assertEqual(edges[0].target_ref, "exc2")  # last in the excluded list
        self.assertEqual(edges[0].confidence, "low")

    def test_self_referential_edge_when_nothing_is_excluded(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="As I mentioned earlier, it's ready.")],
            excluded=[],
        )
        edges = build_cue_phrase_edges(atoms)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].target_ref, "inc1")  # flagged even with no specific target

    def test_no_cue_means_no_edge(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="totally self-contained statement")],
            excluded=[Atom(kind="message", ref="exc1", text="something")],
        )
        self.assertEqual(build_cue_phrase_edges(atoms), ())

    def test_title_atoms_are_never_used_as_a_cue_phrase_target(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="As I mentioned earlier, it's ready.")],
            excluded=[Atom(kind="title", ref="title:original", text="Secret Project Name")],
        )
        edges = build_cue_phrase_edges(atoms)
        self.assertEqual(edges[0].target_ref, "inc1")  # title excluded atoms don't count


class ProvenanceEdgeTests(unittest.TestCase):
    def _events(self, capture_id, turn_index=2):
        return (
            ToolReadEventRecord(
                event_id="tce_1", capture_id=capture_id, turn_index=turn_index,
                tool_name="Read", resource_label="/tmp/x.txt", created_at="2026-09-22T00:00:00Z",
            ),
        )

    def test_edges_only_for_turns_at_or_after_the_tainted_read(self) -> None:
        capture = _capture([("q1", "a1"), ("q2", "a2"), ("q3", "a3")])
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        events = self._events(capture.capture_id, turn_index=2)
        edges = build_provenance_edges(capture, turn_ids, events, frozenset({"tce_1"}))
        self.assertEqual({e.source_ref for e in edges}, {turn_ids[1], turn_ids[2]})
        self.assertTrue(all(e.target_ref == "event:tce_1" for e in edges))
        self.assertTrue(all(e.confidence == "high" for e in edges))

    def test_no_tainted_events_means_no_edges(self) -> None:
        capture = _capture([("q1", "a1")])
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        events = self._events(capture.capture_id)
        self.assertEqual(build_provenance_edges(capture, turn_ids, events, frozenset()), ())


class CascadingCandidatesTests(unittest.TestCase):
    def test_returns_only_edges_pointing_at_flagged_refs(self) -> None:
        atoms = AtomSet(
            included=[
                Atom(kind="message", ref="inc1", text="mentions BLUEHERON"),
                Atom(kind="message", ref="inc2", text="unrelated content"),
            ],
            excluded=[Atom(kind="message", ref="exc1", text="BLUEHERON codename")],
        )
        edges = build_lexical_edges(atoms)
        result = cascading_candidates(edges, frozenset({"exc1"}))
        self.assertEqual({e.source_ref for e in result}, {"inc1"})

    def test_empty_flagged_set_returns_nothing(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1", text="mentions BLUEHERON")],
            excluded=[Atom(kind="message", ref="exc1", text="BLUEHERON codename")],
        )
        edges = build_lexical_edges(atoms)
        self.assertEqual(cascading_candidates(edges, frozenset()), ())

    def test_combines_edges_from_multiple_kinds(self) -> None:
        atoms = AtomSet(
            included=[Atom(kind="message", ref="inc1",
                            text="As I mentioned earlier, BLUEHERON is on track.")],
            excluded=[Atom(kind="message", ref="exc1", text="the codename is BLUEHERON")],
        )
        combined = build_lexical_edges(atoms) + build_cue_phrase_edges(atoms)
        result = cascading_candidates(combined, frozenset({"exc1"}))
        self.assertEqual({e.kind for e in result}, {"lexical", "cue_phrase"})


class EndToEndAtomsToGraphTests(unittest.TestCase):
    """The pieces build_atoms() hands off must compose cleanly with the graph."""

    def test_build_atoms_output_feeds_the_graph_correctly(self) -> None:
        capture = _capture(
            [
                ("the codename is BLUEHERON project", "noted"),
                ("As I mentioned earlier, BLUEHERON rollout is on track", "great"),
            ]
        )
        turn_ids = list(dict.fromkeys(m.turn_id for m in capture.messages))
        atoms = build_atoms(capture, (turn_ids[1],), ())  # only turn 2 included
        lexical = build_lexical_edges(atoms)
        cue = build_cue_phrase_edges(atoms)
        self.assertTrue(lexical)
        self.assertTrue(cue)
        self.assertEqual(lexical[0].target_ref, turn_ids[0])
        self.assertEqual(cue[0].target_ref, turn_ids[0])


if __name__ == "__main__":
    unittest.main()
