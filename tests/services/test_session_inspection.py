from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from prism.cli import main
from prism.development import SyntheticSessionSource
from prism.services.session_inspection import SessionInspector


class SessionInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[2]
            / "examples"
            / "sessions"
            / "synthetic_session.json"
        )

    def test_builds_stable_owner_inventory(self) -> None:
        inspection = SessionInspector(SyntheticSessionSource()).inspect(self.source)

        self.assertEqual(inspection.title, "Synthetic shared-agent research session")
        self.assertTrue(inspection.has_instructions)
        self.assertEqual(
            [turn.turn_id for turn in inspection.completed_turns],
            ["turn-1", "turn-2"],
        )
        self.assertEqual(
            [resource.display_name for resource in inspection.resources],
            ["Project notes", "Private notes"],
        )

    def test_json_cli_output_matches_normalized_contract(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["inspect-session", str(self.source), "--json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(payload["completed_turns"]), 2)
        self.assertEqual(payload["completed_turns"][0]["turn_id"], "turn-1")
        self.assertEqual(len(payload["resources"]), 2)


if __name__ == "__main__":
    unittest.main()
