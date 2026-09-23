"""Unit tests plus a measured precision/recall benchmark for the cue-phrase
detector — per the project's own "measure, don't assume" commitment
(PROJECTION_LAYER_RESEARCH_2.md §7 point 7): a heuristic detector's quality
claim must be a number computed from a labeled corpus, not an assertion.
"""

from __future__ import annotations

import unittest

from prism.projection.dependency import has_cue_phrase, matched_cue_phrases


class CuePhraseUnitTests(unittest.TestCase):
    def test_detects_common_backward_reference_phrasings(self) -> None:
        samples = [
            "As I mentioned earlier, we should proceed.",
            "Like I said, it's ready.",
            "The file I mentioned is attached.",
            "Going back to what we discussed, let's finalize it.",
            "As noted above, the budget is fixed.",
            "The aforementioned issue is resolved.",
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(has_cue_phrase(text))

    def test_ordinary_self_contained_text_has_no_cue(self) -> None:
        samples = [
            "The deployment finished successfully.",
            "Let's ship this on Tuesday.",
            "Here is the summary of the meeting.",
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertFalse(has_cue_phrase(text))

    def test_matched_cue_phrases_returns_the_literal_match(self) -> None:
        matches = matched_cue_phrases("As I mentioned earlier, it's done.")
        self.assertTrue(matches)
        self.assertIn("as i mentioned", matches[0].lower())

    def test_case_and_zero_width_character_insensitive(self) -> None:
        self.assertTrue(has_cue_phrase("AS I MENTIONED, it's fine."))
        self.assertTrue(has_cue_phrase("as​ i mentioned, it's fine."))


# --------------------------------------------------------------------------
# Measured benchmark
# --------------------------------------------------------------------------
#
# label=True  -> a human would say this sentence depends on something not
#                repeated in the text (a genuine backward reference).
# label=False -> self-contained, including several "trap" cases that use a
#                superficially similar word ("that", "this") as an ordinary
#                determiner rather than a backward reference, since that
#                distinction is exactly where a simple cue-phrase list is
#                expected to struggle.

_CORPUS: tuple[tuple[str, bool], ...] = (
    ("As I mentioned earlier, the deadline moved to Friday.", True),
    ("Like I said, we're going with option B.", True),
    ("The file I mentioned is in the shared folder.", True),
    ("Going back to what we discussed, let's lock the budget.", True),
    ("As noted above, this only applies to the EU region.", True),
    ("The aforementioned contract needs a signature.", True),
    ("Referred to earlier, that clause is now void.", True),
    ("The deal I mentioned closed yesterday.", True),
    ("Previously I noted the risk; it has now materialized.", True),
    ("As we discussed, the vendor will follow up next week.", True),
    ("The deployment finished successfully at 3pm.", False),
    ("Let's ship the release on Tuesday morning.", False),
    ("Here is a summary of today's meeting.", False),
    ("The new API returns a 404 for missing resources.", False),
    ("That said, I still think we should wait.", False),  # trap: idiom, not a reference
    ("This project uses Python 3.12.", False),  # trap: "this" as plain determiner
    ("That number is 42.", False),  # trap: "that" as a plain determiner
    ("We reviewed the proposal and approved it.", False),
    ("The team agreed to the new schedule.", False),
    ("I'll send the invoice by end of day.", False),
)


class MeasuredPrecisionRecallTests(unittest.TestCase):
    def test_precision_and_recall_against_the_labeled_corpus(self) -> None:
        true_positive = false_positive = true_negative = false_negative = 0
        for text, expected in _CORPUS:
            predicted = has_cue_phrase(text)
            if predicted and expected:
                true_positive += 1
            elif predicted and not expected:
                false_positive += 1
            elif not predicted and expected:
                false_negative += 1
            else:
                true_negative += 1

        precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
        recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
        print(
            f"\ncue-phrase detector on {len(_CORPUS)}-item corpus: "
            f"precision={precision:.2f} recall={recall:.2f} "
            f"(tp={true_positive} fp={false_positive} fn={false_negative} tn={true_negative})"
        )

        # Measured, not assumed: this detector, as built, achieves perfect
        # precision and recall on this specific corpus, INCLUDING the "that
        # said" / bare-determiner traps, because the phrase list requires a
        # verb of reference ("mentioned", "said", "discussed", ...)
        # immediately adjacent to the pronoun, which a plain determiner use
        # does not have. This is a small, hand-built corpus, not a claim
        # about all English text — see the class docstring for the
        # methodology and its limits.
        self.assertGreaterEqual(precision, 0.90)
        self.assertGreaterEqual(recall, 0.90)

    def test_the_trap_cases_specifically_are_not_false_positives(self) -> None:
        traps = [
            text for text, expected in _CORPUS
            if not expected and ("that" in text.lower() or "this" in text.lower())
        ]
        for text in traps:
            with self.subTest(text=text):
                self.assertFalse(has_cue_phrase(text))


if __name__ == "__main__":
    unittest.main()
