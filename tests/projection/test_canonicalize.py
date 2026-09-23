from __future__ import annotations

import base64
import unittest

from prism.projection.canonicalize import (
    canonicalize,
    decode_and_rescan_text,
    prepare_for_scanning,
)


class CanonicalizeTests(unittest.TestCase):
    def test_strips_zero_width_characters(self) -> None:
        self.assertEqual(canonicalize("A​B‌C‍D"), "ABCD")

    def test_strips_bom_and_soft_hyphen_and_word_joiner(self) -> None:
        self.assertEqual(canonicalize("﻿A­B⁠C"), "ABC")

    def test_nfkc_folds_fullwidth_forms(self) -> None:
        self.assertEqual(canonicalize("ＡＢＣ"), "ABC")

    def test_ordinary_text_is_unchanged(self) -> None:
        text = "The quick brown fox jumps over the lazy dog. 123!"
        self.assertEqual(canonicalize(text), text)

    def test_idempotent(self) -> None:
        text = "café ​müller"
        once = canonicalize(text)
        twice = canonicalize(once)
        self.assertEqual(once, twice)


class DecodeAndRescanTests(unittest.TestCase):
    def test_appends_decoded_base64(self) -> None:
        secret = "the-real-secret-value-right-here"
        encoded = base64.b64encode(secret.encode()).decode()
        widened = decode_and_rescan_text(f"blob: {encoded}")
        self.assertIn(secret, widened)
        self.assertIn(encoded, widened)  # original text is preserved, not replaced

    def test_appends_decoded_hex(self) -> None:
        secret = "hex-encoded-secret-string"
        encoded = secret.encode().hex()
        widened = decode_and_rescan_text(f"payload={encoded}")
        self.assertIn(secret, widened)

    def test_short_base64_like_runs_are_ignored(self) -> None:
        # Below the length floor; avoids treating short words as encoded data.
        widened = decode_and_rescan_text("word ABCDEFGH more text")
        self.assertEqual(widened, "word ABCDEFGH more text")

    def test_invalid_base64_padding_does_not_raise(self) -> None:
        widened = decode_and_rescan_text("garbage: " + "A" * 30)
        self.assertIsInstance(widened, str)

    def test_non_utf8_decoded_bytes_are_dropped_not_raised(self) -> None:
        # Valid base64 that decodes to non-UTF-8 bytes must not crash scanning.
        encoded = base64.b64encode(bytes(range(200, 230))).decode()
        widened = decode_and_rescan_text(f"data: {encoded}")
        self.assertIn(encoded, widened)

    def test_text_with_nothing_encoded_is_returned_unchanged(self) -> None:
        text = "just an ordinary sentence with no encoded payloads at all"
        self.assertEqual(decode_and_rescan_text(text), text)


class PrepareForScanningTests(unittest.TestCase):
    def test_composes_canonicalize_and_decode(self) -> None:
        secret = "composed-pipeline-secret"
        encoded = base64.b64encode(secret.encode()).decode()
        widened = prepare_for_scanning(f"A​B {encoded}")
        self.assertIn("AB", widened)
        self.assertIn(secret, widened)


if __name__ == "__main__":
    unittest.main()
