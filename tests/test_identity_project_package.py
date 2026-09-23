"""Offline boundary tests for the one fixed synthetic project transfer."""

import hashlib
import importlib.util
import io
import os
import shutil
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "identity_project_package", ROOT / "scripts/gcp/identity-project-package.py"
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class FixedProjectPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.transfer = self.base / "project.tar"
        self.transfer_sha = tool.package(self.transfer)

    def rewrite(self, edit):
        result = self.base / "changed.tar"
        with (
            tarfile.open(self.transfer, "r:") as source,
            tarfile.open(result, "w", format=tarfile.USTAR_FORMAT) as target,
        ):
            for member in source:
                data = None if member.isdir() else source.extractfile(member).read()
                replacement = edit(member, data)
                if replacement is None:
                    continue
                member, data = replacement
                member.size = 0 if data is None else len(data)
                target.addfile(member, None if data is None else io.BytesIO(data))
        return result, hashlib.sha256(result.read_bytes()).hexdigest()

    def test_package_is_repeatable_and_pins_unlisted_and_private_files(self):
        self.assertEqual(tool.PROJECT_SHA256, tool.inventory_sha256())
        self.assertEqual(
            set(tool.checked_archive(self.transfer, self.transfer_sha)),
            set(tool.FILE_HASHES),
        )
        other = self.base / "other.tar"
        self.assertEqual(self.transfer_sha, tool.package(other))
        self.assertEqual(self.transfer.read_bytes(), other.read_bytes())
        self.assertEqual(self.transfer.stat().st_mode & 0o777, 0o600)
        self.assertIn("unlisted-canary.txt", tool.FILE_HASHES)
        self.assertIn("private/notes.txt", tool.FILE_HASHES)

    def test_missing_extra_tampered_and_traversal_members_are_denied(self):
        cases = (
            lambda member, data: None if member.name == "README.md" else (member, data),
            lambda member, data: (
                (self.rename(member, "../escape"), data)
                if member.name == "README.md"
                else (member, data)
            ),
            lambda member, data: (
                (member, b"changed") if member.name == "README.md" else (member, data)
            ),
            lambda member, data: (
                (self.rename(member, "extra.txt"), data)
                if member.name == "README.md"
                else (member, data)
            ),
        )
        for edit in cases:
            with self.subTest(edit=edit):
                path, digest = self.rewrite(edit)
                with self.assertRaises(ValueError):
                    tool.checked_archive(path, digest)
                path.unlink()

    @staticmethod
    def rename(member, name):
        member.name = name
        return member

    def test_links_and_modes_are_denied(self):
        def symlink(member, data):
            if member.name == "README.md":
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                return member, None
            return member, data

        def public_mode(member, data):
            if member.name == "README.md":
                member.mode = 0o644
            return member, data

        for edit in (symlink, public_mode):
            with self.subTest(edit=edit):
                path, digest = self.rewrite(edit)
                with self.assertRaises(ValueError):
                    tool.checked_archive(path, digest)
                path.unlink()

    def test_source_extra_missing_tampered_and_symlink_are_denied(self):
        fixture = self.base / "fixture"
        for mutation in ("extra", "missing", "tampered", "symlink"):
            with self.subTest(mutation=mutation):
                shutil.copytree(tool.FIXTURE, fixture)
                if mutation == "extra":
                    (fixture / "extra.txt").write_text("extra")
                elif mutation == "missing":
                    (fixture / "README.md").unlink()
                elif mutation == "tampered":
                    (fixture / "private/notes.txt").write_text("changed")
                else:
                    (fixture / "README.md").unlink()
                    (fixture / "README.md").symlink_to("/etc/passwd")
                with self.assertRaises(ValueError):
                    tool.checked_contents(fixture)
                shutil.rmtree(fixture)

    def test_install_exact_private_tree_and_refuse_overwrite(self):
        parent = self.base / "identity"
        parent.mkdir(mode=0o700)
        destination = parent / "project"
        with (
            patch.object(tool, "PROJECT_ROOT", destination),
            patch.object(tool, "OWNER_UID", os.geteuid()),
        ):
            self.assertEqual(
                tool.install(self.transfer, self.transfer_sha), tool.PROJECT_SHA256
            )
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            for directory in tool.DIRECTORIES:
                self.assertEqual(
                    (destination / directory).stat().st_mode & 0o777, 0o700
                )
            for name in tool.FILE_HASHES:
                info = (destination / name).stat()
                self.assertTrue(stat.S_ISREG(info.st_mode))
                self.assertEqual(info.st_mode & 0o777, 0o600)
                self.assertEqual(info.st_nlink, 1)
            with self.assertRaises(FileExistsError):
                tool.install(self.transfer, self.transfer_sha)
            self.assertEqual(
                tool.checked_contents(destination),
                tool.checked_archive(self.transfer, self.transfer_sha),
            )

    def test_transfer_hash_and_existing_target_are_denied(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            tool.checked_archive(self.transfer, "0" * 64)
        linked = self.base / "linked.tar"
        linked.symlink_to(self.transfer)
        with self.assertRaisesRegex(ValueError, "regular file"):
            tool.checked_archive(linked, self.transfer_sha)

    def test_install_rejects_symlink_parent(self):
        actual = self.base / "actual"
        actual.mkdir(mode=0o700)
        linked = self.base / "linked"
        linked.symlink_to(actual, target_is_directory=True)
        with (
            patch.object(tool, "PROJECT_ROOT", linked / "project"),
            patch.object(tool, "OWNER_UID", os.geteuid()),
            self.assertRaisesRegex(ValueError, "parent"),
        ):
            tool.install(self.transfer, self.transfer_sha)
        self.assertFalse((actual / "project").exists())

    def test_partial_stage_failure_leaves_target_absent_and_retry_succeeds(self):
        parent = self.base / "identity"
        parent.mkdir(mode=0o700)
        destination = parent / "project"
        with (
            patch.object(tool, "PROJECT_ROOT", destination),
            patch.object(tool, "OWNER_UID", os.geteuid()),
        ):
            with (
                patch.object(tool.os, "fsync", side_effect=OSError("interrupted")),
                self.assertRaisesRegex(OSError, "interrupted"),
            ):
                tool.install(self.transfer, self.transfer_sha)
            self.assertFalse(destination.exists())
            self.assertEqual(list(parent.iterdir()), [])
            self.assertEqual(
                tool.install(self.transfer, self.transfer_sha), tool.PROJECT_SHA256
            )

    def test_racing_existing_target_is_never_replaced(self):
        parent = self.base / "identity"
        parent.mkdir(mode=0o700)
        destination = parent / "project"
        original_rename = tool.rename_no_replace

        def create_target_then_rename(stage, target):
            target.mkdir(mode=0o700)
            (target / "keep.txt").write_text("existing")
            original_rename(stage, target)

        with (
            patch.object(tool, "PROJECT_ROOT", destination),
            patch.object(tool, "OWNER_UID", os.geteuid()),
            patch.object(
                tool, "rename_no_replace", side_effect=create_target_then_rename
            ),
            self.assertRaises(FileExistsError),
        ):
            tool.install(self.transfer, self.transfer_sha)
        self.assertEqual((destination / "keep.txt").read_text(), "existing")
        self.assertEqual(list(parent.iterdir()), [destination])

    def test_fifo_swap_after_lstat_is_denied_without_blocking(self):
        swapped = self.base / "swapped.tar"
        shutil.copyfile(self.transfer, swapped)
        original_open = tool.os.open

        def swap_before_open(path, flags, *args):
            if Path(path) == swapped:
                swapped.unlink()
                os.mkfifo(swapped)
            return original_open(path, flags, *args)

        with (
            patch.object(tool.os, "open", side_effect=swap_before_open),
            self.assertRaisesRegex(ValueError, "changed type"),
        ):
            tool.checked_archive(swapped, self.transfer_sha)


if __name__ == "__main__":
    unittest.main()
