from __future__ import annotations

import unittest

from prism.projection.atoms import build_atoms
from prism.projection.detectors import Atom, FindingClass, FindingSeverity, run_detectors
from prism.projection.exclusion import ExclusionVocabulary, detect_exclusion_derived
from prism.models.projection import ProjectionAttachment


def _atom(text: str, ref: str = "msg_00001") -> Atom:
    return Atom(kind="message", ref=ref, text=text)


class SecretDetectorTests(unittest.TestCase):
    def test_detects_aws_key_and_is_blocking(self) -> None:
        findings = run_detectors([_atom("here is AKIAJVQVGF6NXQZXKMPS for you")])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_class.value, "secret")
        self.assertEqual(findings[0].severity, FindingSeverity.BLOCK)

    def test_detects_github_slack_google_stripe_and_private_key(self) -> None:
        samples = [
            "ghp_" + "a" * 36,
            "xoxb-" + "1234567890-abcdefghij",
            "AIza" + "b" * 35,
            "sk_live_" + "c" * 24,
            "-----BEGIN RSA PRIVATE KEY-----",
        ]
        for sample in samples:
            with self.subTest(sample=sample[:12]):
                findings = run_detectors([_atom(f"token: {sample}")])
                self.assertTrue(any(f.finding_class.value == "secret" for f in findings))

    def test_jwt_and_keyword_assignment(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ_abc123"
        findings = run_detectors([_atom(f"auth header: {jwt}")])
        self.assertTrue(any("jwt" in f.detector for f in findings))
        findings2 = run_detectors([_atom("api_key: 'sk_abcdef0123456789ZZ'")])
        self.assertTrue(any(f.finding_class.value == "secret" for f in findings2))

    def test_ordinary_text_has_no_secret_findings(self) -> None:
        findings = run_detectors([_atom("Let's discuss the roadmap for next quarter.")])
        self.assertEqual([f for f in findings if f.finding_class.value == "secret"], [])


class UrlSecretDetectorTests(unittest.TestCase):
    def test_embedded_credentials(self) -> None:
        findings = run_detectors([_atom("fetch https://user:hunter2@example.com/data")])
        self.assertTrue(any(f.finding_class.value == "url-secret" for f in findings))

    def test_token_query_parameter(self) -> None:
        findings = run_detectors(
            [_atom("see https://example.com/report?access_token=abcdEFGH12345678")]
        )
        self.assertTrue(any(f.finding_class.value == "url-secret" for f in findings))

    def test_plain_url_is_clean(self) -> None:
        findings = run_detectors([_atom("see https://example.com/report?id=42")])
        self.assertEqual([f for f in findings if f.finding_class.value == "url-secret"], [])


class PiiDetectorTests(unittest.TestCase):
    def test_email_detected(self) -> None:
        findings = run_detectors([_atom("reach me at alice.smith@corp-internal.io please")])
        self.assertTrue(any(f.finding_class.value == "pii-direct" for f in findings))

    def test_luhn_valid_card_detected_invalid_not(self) -> None:
        valid = run_detectors([_atom("card 4111 1111 1111 1111 on file")])
        self.assertTrue(any(f.finding_class.value == "pii-direct" for f in valid))
        invalid = run_detectors([_atom("card 4111 1111 1111 1112 on file")])
        self.assertEqual([f for f in invalid if "card" in f.detector], [])

    def test_iban_detected(self) -> None:
        findings = run_detectors([_atom("IBAN DE89370400440532013000 for wire")])
        self.assertTrue(any(f.finding_class.value == "pii-direct" for f in findings))


class EnvIdentifierDetectorTests(unittest.TestCase):
    def test_home_directory_and_private_ip(self) -> None:
        findings = run_detectors(
            [_atom("logs at /Users/kishore/data and host 192.168.1.42")]
        )
        classes = {f.finding_class.value for f in findings}
        self.assertIn("env-identifier", classes)
        self.assertTrue(any("home_dir" in f.detector for f in findings))
        self.assertTrue(any("private_ip" in f.detector for f in findings))

    def test_public_ip_not_flagged(self) -> None:
        findings = run_detectors([_atom("server at 8.8.8.8")])
        self.assertEqual([f for f in findings if f.finding_class.value == "env-identifier"], [])


class DeterminismAndDedupeTests(unittest.TestCase):
    def test_run_is_deterministic_and_same_match_in_one_atom_is_deduped(self) -> None:
        atom = _atom("key AKIAJVQVGF6NXQZXKMPS and again AKIAJVQVGF6NXQZXKMPS")
        first = run_detectors([atom])
        second = run_detectors([atom])
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)  # identical match collapses to one finding


class CanonicalizationTests(unittest.TestCase):
    def test_zero_width_characters_do_not_defeat_the_secret_pattern(self) -> None:
        obfuscated = "AKIA​JVQVGF6NXQZXKMPS"  # zero-width space mid-token
        findings = run_detectors([_atom(f"key: {obfuscated}")])
        self.assertTrue(any(f.finding_class.value == "secret" for f in findings))

    def test_fullwidth_compatibility_form_is_normalized(self) -> None:
        # Fullwidth Latin letters (U+FF21.. etc) are NFKC-compatibility-equivalent
        # to ASCII, unlike a genuine cross-script confusable.
        fullwidth = "ＡＫＩＡJVQVGF6NXQZXKMPS"  # "AKIA" in fullwidth + rest
        findings = run_detectors([_atom(f"key: {fullwidth}")])
        self.assertTrue(any(f.finding_class.value == "secret" for f in findings))

    def test_base64_encoded_secret_is_caught_by_decode_and_rescan(self) -> None:
        import base64

        encoded = base64.b64encode(b"AKIAJVQVGF6NXQZXKMPS is the key").decode()
        findings = run_detectors([_atom(f"here's the config blob: {encoded}")])
        self.assertTrue(any(f.finding_class.value == "secret" for f in findings))

    def test_cross_script_homoglyph_is_a_known_unsolved_gap(self) -> None:
        # Documents the limit stated in canonicalize.py: this is not solved.
        homoglyph = "АKIAJVQVGF6NXQZXKMPS"  # Cyrillic А (U+0410) instead of A
        findings = run_detectors([_atom(f"key: {homoglyph}")])
        self.assertEqual([f for f in findings if f.finding_class.value == "secret"], [])


class DenylistScopingTests(unittest.TestCase):
    def test_aws_documented_example_key_is_suppressed(self) -> None:
        findings = run_detectors([_atom("here is AKIAIOSFODNN7EXAMPLE for you")])
        self.assertEqual([f for f in findings if f.finding_class.value == "secret"], [])

    def test_rfc2606_example_email_is_suppressed(self) -> None:
        findings = run_detectors([_atom("contact test@example.com for details")])
        self.assertEqual([f for f in findings if f.finding_class.value == "pii-direct"], [])

    def test_example_domain_does_not_suppress_a_real_url_secret(self) -> None:
        # Regression guard: an earlier version of the denylist applied every
        # entry globally and silently suppressed this.
        findings = run_detectors(
            [_atom("fetch https://user:hunter2@example.com/data")]
        )
        self.assertTrue(any(f.finding_class.value == "url-secret" for f in findings))


class ExclusionDerivedTests(unittest.TestCase):
    def test_flags_distinctive_terms_shared_with_excluded_content(self) -> None:
        vocabulary = ExclusionVocabulary.build([_atom("the codename is BLUEHERON project", "excluded")])
        findings = detect_exclusion_derived(
            _atom("we discussed BLUEHERON rollout timing"), vocabulary
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, FindingSeverity.WARN)

    def test_short_and_common_words_are_not_flagged(self) -> None:
        vocabulary = ExclusionVocabulary.build([_atom("the quick brown fox and a cat", "excluded")])
        findings = detect_exclusion_derived(_atom("a fox and a cat ran"), vocabulary)
        self.assertEqual(findings, [])  # "fox"/"cat" < 5 chars, "the"/"and" are stopwords

    def test_empty_vocabulary_flags_nothing(self) -> None:
        vocabulary = ExclusionVocabulary.build([])
        self.assertFalse(vocabulary)
        self.assertEqual(detect_exclusion_derived(_atom("anything distinctive here"), vocabulary), [])

    def test_case_and_unicode_normalization(self) -> None:
        vocabulary = ExclusionVocabulary.build([_atom("Café Müller invoice", "excluded")])
        findings = detect_exclusion_derived(_atom("the café müller total"), vocabulary)
        self.assertTrue(findings)

    def test_a_repeated_term_in_one_atom_is_one_finding_not_one_per_occurrence(self) -> None:
        # Regression: a real capture (a broad excluded turn sharing ordinary
        # vocabulary with one long included answer) produced over a
        # thousand near-duplicate findings — the same term flagged once per
        # raw regex occurrence in a single message — burying a genuine,
        # distinct leak (a name) under noise. One finding per (atom, term).
        vocabulary = ExclusionVocabulary.build(
            [_atom("this touches on research topics", "excluded")]
        )
        long_included = _atom(
            "research is central here. " * 20 + "more research follows.", "included"
        )
        findings = detect_exclusion_derived(long_included, vocabulary)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_class, FindingClass.EXCLUSION_DERIVED)

    def test_different_terms_in_the_same_atom_each_still_get_a_finding(self) -> None:
        vocabulary = ExclusionVocabulary.build(
            [_atom("professor jackson leads a confidential project", "excluded")]
        )
        findings = detect_exclusion_derived(
            _atom("you were working with professor jackson on a confidential project", "included"),
            vocabulary,
        )
        terms = {f.detail.split("'")[1] for f in findings}
        self.assertEqual(terms, {"professor", "jackson", "confidential", "project"})


class BuildAtomsTests(unittest.TestCase):
    def _capture(self):
        from prism.models.capture import (
            AdapterCapture,
            AdapterMessage,
            CaptureMethod,
            MessageRole,
            Platform,
        )
        from prism.services.normalization import CaptureNormalizer

        def turn(i, u, a):
            return [
                AdapterMessage(source_message_id=f"s{i}u", turn_index=i, ordinal=2 * i - 1,
                                role=MessageRole.USER, content=u),
                AdapterMessage(source_message_id=f"s{i}a", turn_index=i, ordinal=2 * i,
                                role=MessageRole.ASSISTANT, content=a),
            ]

        messages = turn(1, "hello", "hi there") + turn(2, "secret stuff", "ack")
        return CaptureNormalizer().normalize(
            "test-adapter/0.1",
            AdapterCapture(
                platform=Platform.CHATGPT,
                method=CaptureMethod.SYNTHETIC,
                source_fingerprint="sha256:" + "a" * 64,
                conversation_ref="convref_abcdefgh",
                title="Original Title",
                messages=tuple(messages),
            ),
        )

    def test_excluded_turns_and_title_split_correctly(self) -> None:
        capture = self._capture()
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        atoms = build_atoms(capture, (turn_ids[0],), ())
        self.assertEqual(len(atoms.included), 2)  # title + turn 1
        self.assertEqual(len(atoms.excluded), 1)  # turn 2
        self.assertIn("secret stuff", atoms.excluded[0].text)

    def test_title_override_makes_original_title_an_excluded_atom(self) -> None:
        capture = self._capture()
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        atoms = build_atoms(capture, turn_ids, (), title_override="New public title")
        self.assertEqual(atoms.included[0].text, "New public title")
        original_title_atoms = [a for a in atoms.excluded if a.ref == "title:original"]
        self.assertEqual(len(original_title_atoms), 1)
        self.assertEqual(original_title_atoms[0].text, "Original Title")

    def test_unchanged_title_is_not_duplicated_as_excluded(self) -> None:
        capture = self._capture()
        turn_ids = tuple(dict.fromkeys(m.turn_id for m in capture.messages))
        atoms = build_atoms(capture, turn_ids, (), title_override="Original Title")
        self.assertEqual([a for a in atoms.excluded if a.kind == "title"], [])

    def test_attachments_become_included_resource_atoms(self) -> None:
        capture = self._capture()
        attachment = ProjectionAttachment(
            attachment_id="att_0123456789abcdef01234567",
            display_name="notes.md",
            media_type="text/markdown",
            content="shared notes",
        )
        atoms = build_atoms(capture, (), (attachment,))
        resource_atoms = [a for a in atoms.included if a.kind == "resource"]
        self.assertEqual(len(resource_atoms), 1)
        self.assertEqual(resource_atoms[0].text, "shared notes")


if __name__ == "__main__":
    unittest.main()
