from __future__ import annotations

import tempfile
import unittest
import warnings
import zipfile
import stat
import unicodedata
from pathlib import Path

from prism.archives import ArchiveLimits, SafeArchiveReader
from prism.exceptions import UnsafeArchiveError


class SafeArchiveReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sample = (
            Path(__file__).resolve().parents[2]
            / "examples"
            / "chatgpt"
            / "synthetic_export.json"
        )

    @staticmethod
    def _read(reader: SafeArchiveReader, source: Path):
        return reader.read_document(
            source,
            document_name="conversations.json",
            allow_direct_json=True,
        )

    def test_reads_conversations_json_without_extracting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "export.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(self.sample, "conversations.json")

            document = self._read(SafeArchiveReader(), archive_path)

            self.assertEqual(document.name, "conversations.json")
            self.assertIn(b"prism.synthetic-chatgpt-export.v1", document.content)
            self.assertFalse((Path(directory) / "conversations.json").exists())

    def test_rejects_parent_traversal_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../conversations.json", b"{}")

            with self.assertRaises(UnsafeArchiveError):
                self._read(SafeArchiveReader(), archive_path)

    def test_rejects_other_unsafe_member_paths(self) -> None:
        unsafe_names = (
            "/conversations.json",
            "folder\\conversations.json",
            "folder//conversations.json",
            "./conversations.json",
            "C:/conversations.json",
        )
        for index, name in enumerate(unsafe_names):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive_path = Path(directory) / f"unsafe-{index}.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(name, b"{}")

                with self.assertRaises(UnsafeArchiveError):
                    self._read(SafeArchiveReader(), archive_path)

    def test_rejects_unicode_normalized_name_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "unicode-collision.zip"
            composed = "caf\N{LATIN SMALL LETTER E WITH ACUTE}.json"
            decomposed = unicodedata.normalize("NFD", composed)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(composed, b"{}")
                archive.writestr(decomposed, b"{}")
                archive.writestr("conversations.json", b"{}")

            with self.assertRaises(UnsafeArchiveError):
                self._read(SafeArchiveReader(), archive_path)

    def test_rejects_symbolic_link_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "symlink-member.zip"
            link = zipfile.ZipInfo("linked-conversations.json")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(link, "conversations.json")
                archive.writestr("conversations.json", b"{}")

            with self.assertRaises(UnsafeArchiveError):
                self._read(SafeArchiveReader(), archive_path)

    def test_rejects_excessive_compression_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "compressed.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("conversations.json", b"0" * 10_000)
            reader = SafeArchiveReader(
                ArchiveLimits(
                    max_archive_bytes=100_000,
                    max_entries=10,
                    max_entry_bytes=20_000,
                    max_expanded_bytes=20_000,
                    max_compression_ratio=2,
                )
            )

            with self.assertRaises(UnsafeArchiveError):
                self._read(reader, archive_path)

    def test_rejects_symbolic_link_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            link = root / "linked.json"
            link.symlink_to(self.sample)

            with self.assertRaises(UnsafeArchiveError):
                self._read(SafeArchiveReader(), link)

    def test_rejects_duplicate_normalized_member_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "duplicate.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("conversations.json", b"{}")
                    archive.writestr("conversations.json", b"{}")

            with self.assertRaises(UnsafeArchiveError):
                self._read(SafeArchiveReader(), archive_path)

    def test_rejects_excessive_actual_entry_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "large.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("conversations.json", b"0123456789")
            reader = SafeArchiveReader(
                ArchiveLimits(
                    max_archive_bytes=10_000,
                    max_entries=10,
                    max_entry_bytes=5,
                    max_expanded_bytes=10_000,
                    max_compression_ratio=100,
                )
            )

            with self.assertRaises(UnsafeArchiveError):
                self._read(reader, archive_path)


if __name__ == "__main__":
    unittest.main()
