from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from prism.cli import _normalize_shared_link_input, main
from prism.database import PrismDatabase
from prism.models.capture import RemoteCaptureSource
from prism.providers.chatgpt.shared_link import ChatGPTSharedLinkAdapter
from prism.services.capture import CaptureService
from prism.services.drafts import DraftService
from prism.services.durable_shared_link_capture import DurableSharedLinkCaptureService
from prism.services.normalization import CaptureNormalizer
from prism.services.projection import ProjectionService
from prism.services.shared_link_capture import SharedLinkCaptureService
from prism.storage import InMemoryCaptureCandidateStore, LocalCaptureStore

from tests.providers.chatgpt.shared_link.helpers import (
    StaticFetcher,
    synthetic_shared_page,
)


class CaptureCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sample = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "chatgpt"
            / "synthetic_export.json"
        )

    def test_json_inventory_is_scriptable(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(output):
            exit_code = main(
                [
                    "capture",
                    "list",
                    str(self.sample),
                    "--data-dir",
                    directory,
                    "--json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(payload["conversations"]), 3)
        self.assertNotIn("UNSELECTED_PRIVATE_MARKER_7429", output.getvalue())
        self.assertFalse((Path(directory) / "prism.db").exists())

    def test_interactive_selection_creates_owner_only_capture(self) -> None:
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with patch("builtins.input", return_value="1"), redirect_stdout(output):
                exit_code = main(
                    [
                        "capture",
                        "select",
                        str(self.sample),
                        "--data-dir",
                        directory,
                    ]
                )
            database_path = Path(directory) / "prism.db"
            with closing(sqlite3.connect(database_path)) as connection:
                capture_count = connection.execute(
                    "SELECT COUNT(*) FROM captures"
                ).fetchone()[0]
                draft_count = connection.execute(
                    "SELECT COUNT(*) FROM share_drafts"
                ).fetchone()[0]

        self.assertEqual(exit_code, 0)
        self.assertEqual(capture_count, 1)
        self.assertEqual(draft_count, 1)
        self.assertIn("Selected: Prism shared-agent research", output.getvalue())

    def test_shared_link_review_runs_capture_selection_and_exact_preview(self) -> None:
        output = io.StringIO()
        shared_url = (
            "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"
        )
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            database = PrismDatabase(data_dir / "prism.db")
            shared_link_service = DurableSharedLinkCaptureService(
                ChatGPTSharedLinkAdapter(
                    fetcher=StaticFetcher(synthetic_shared_page())
                ),
                CaptureNormalizer(),
                database,
            )
            services = (shared_link_service, DraftService(database))
            with (
                patch("prism.cli._shared_link_services", return_value=services),
                patch("prism.cli.getpass.getpass", return_value=shared_url),
                patch("builtins.input", side_effect=["", "yes", "2"]),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "shared-link",
                        "review",
                        "--data-dir",
                        directory,
                        "--hidden-input",
                    ]
                )
            drafts = DraftService(PrismDatabase(data_dir / "prism.db")).list()

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].revision, 2)
        self.assertEqual(drafts[0].previewed_revision, 2)
        self.assertIsNotNone(drafts[0].preview_hash)
        self.assertIn("Candidate capture (temporary", rendered)
        self.assertIn("Second question", rendered)
        self.assertIn("Second answer", rendered)
        self.assertIn("Preview only", rendered)
        snapshot_output = rendered.split("Exact recipient-visible snapshot preview:", 1)[1]
        self.assertNotIn("First question", snapshot_output)

    def test_shared_link_candidate_can_resume_in_a_new_service_instance(self) -> None:
        output = io.StringIO()
        shared_url = (
            "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"
        )
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            database = PrismDatabase(data_dir / "prism.db")
            first = DurableSharedLinkCaptureService(
                ChatGPTSharedLinkAdapter(
                    fetcher=StaticFetcher(synthetic_shared_page())
                ),
                CaptureNormalizer(),
                database,
            )
            candidate = first.inspect(RemoteCaptureSource(url=shared_url))
            restarted_database = PrismDatabase(data_dir / "prism.db")
            restarted = DurableSharedLinkCaptureService(
                ChatGPTSharedLinkAdapter(
                    fetcher=StaticFetcher(b"unused")
                ),
                CaptureNormalizer(),
                restarted_database,
            )
            services = (restarted, DraftService(restarted_database))
            with (
                patch("prism.cli._shared_link_services", return_value=services),
                patch("builtins.input", side_effect=["", "yes", "1"]),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "shared-link",
                        "resume",
                        candidate.import_id,
                        "--data-dir",
                        directory,
                    ]
                )

            drafts = DraftService(PrismDatabase(data_dir / "prism.db")).list()

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(drafts), 1)
        self.assertIn("First question", output.getvalue())

    def test_visible_shared_link_input_does_not_use_password_prompt(self) -> None:
        output = io.StringIO()
        shared_url = (
            "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"
        )
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            capture_store = LocalCaptureStore(data_dir)
            services = (
                SharedLinkCaptureService(
                    ChatGPTSharedLinkAdapter(
                        fetcher=StaticFetcher(synthetic_shared_page())
                    ),
                    InMemoryCaptureCandidateStore(),
                    CaptureService(
                        adapters={},
                        normalizer=CaptureNormalizer(),
                        store=capture_store,
                    ),
                ),
                ProjectionService(capture_store),
            )
            with (
                patch("prism.cli._shared_link_services", return_value=services),
                patch("prism.cli.getpass.getpass") as hidden_prompt,
                patch(
                    "builtins.input",
                    side_effect=[shared_url, "", "yes", "1"],
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "shared-link",
                        "review",
                        "--data-dir",
                        directory,
                        "--visible-input",
                    ]
                )

        self.assertEqual(exit_code, 0)
        hidden_prompt.assert_not_called()
        self.assertIn("First question", output.getvalue())

    def test_normalizes_safe_clipboard_wrappers_around_shared_link(self) -> None:
        shared_url = (
            "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"
        )
        clipboard_values = (
            shared_url,
            f"  {shared_url}  ",
            f"<{shared_url}>",
            f'"{shared_url}"',
            f"[ChatGPT conversation]({shared_url})",
            f"\x1b[200~{shared_url}\x1b[201~",
            shared_url.replace("/share/", "/share/\u200b"),
        )
        for value in clipboard_values:
            with self.subTest(value=value):
                self.assertEqual(_normalize_shared_link_input(value), shared_url)


if __name__ == "__main__":
    unittest.main()
