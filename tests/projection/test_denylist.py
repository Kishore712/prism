from __future__ import annotations

import unittest

from prism.projection.denylist import benign_reason, is_known_benign


class DenylistTests(unittest.TestCase):
    def test_aws_example_key_is_benign_for_secret_class(self) -> None:
        self.assertTrue(is_known_benign("secret", "AKIAIOSFODNN7EXAMPLE"))

    def test_aws_example_key_is_case_insensitive(self) -> None:
        self.assertTrue(is_known_benign("secret", "akiaiosfodnn7example"))

    def test_aws_example_key_is_benign_regardless_of_scope_argument(self) -> None:
        # Unambiguous vendor-documented identifiers are safe in any class.
        self.assertTrue(is_known_benign("pii-direct", "AKIAIOSFODNN7EXAMPLE"))

    def test_real_looking_key_is_not_suppressed(self) -> None:
        self.assertFalse(is_known_benign("secret", "AKIAJVQVGF6NXQZXKMPS"))

    def test_example_domain_is_scoped_to_pii_direct_only(self) -> None:
        self.assertTrue(is_known_benign("pii-direct", "test@example.com"))
        self.assertFalse(is_known_benign("url-secret", "https://user:pw@example.com/x"))

    def test_reserved_phone_number_is_benign(self) -> None:
        self.assertTrue(is_known_benign("pii-direct", "call 555-0142 now"))

    def test_ordinary_phone_number_is_not_suppressed(self) -> None:
        self.assertFalse(is_known_benign("pii-direct", "call 555-9876 now"))

    def test_benign_reason_names_the_source(self) -> None:
        reason = benign_reason("secret", "AKIAIOSFODNN7EXAMPLE")
        self.assertIsNotNone(reason)
        self.assertIn("AWS", reason)

    def test_benign_reason_is_none_for_a_real_finding(self) -> None:
        self.assertIsNone(benign_reason("secret", "AKIAJVQVGF6NXQZXKMPS"))


if __name__ == "__main__":
    unittest.main()
