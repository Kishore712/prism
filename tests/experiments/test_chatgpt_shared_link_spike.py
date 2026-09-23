from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any


MODULE_PATH = (
    Path(__file__).parents[2]
    / "experiments"
    / "chatgpt_shared_link"
    / "inspect_shared_link.py"
)
SPEC = importlib.util.spec_from_file_location("prism_chatgpt_shared_link_spike", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SPIKE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SPIKE
SPEC.loader.exec_module(SPIKE)

class _FlatEncoder:
    def __init__(self) -> None:
        self.values: list[Any] = []

    def encode(self, value: Any) -> int:
        index = len(self.values)
        self.values.append(None)
        if isinstance(value, dict):
            encoded: dict[str, int] = {}
            self.values[index] = encoded
            for key, item in value.items():
                key_index = self.encode(key)
                encoded[f"_{key_index}"] = self.encode(item)
        elif isinstance(value, list):
            encoded_list: list[int] = []
            self.values[index] = encoded_list
            encoded_list.extend(self.encode(item) for item in value)
        else:
            self.values[index] = value
        return index


def _message(
    message_id: str,
    role: str,
    text: str,
    exchange_id: str,
    *,
    hidden: bool = False,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "author": {"role": role},
        "content": {"content_type": "text", "parts": [text]},
        "status": "finished_successfully",
        "metadata": {
            "turn_exchange_id": exchange_id,
            "is_visually_hidden_from_conversation": hidden,
            "is_redacted": False,
            "content_references": [],
        },
    }


def _synthetic_page() -> bytes:
    nodes = [
        {"id": "root-node", "message": None, "parent": None, "children": []},
        {
            "id": "system-node",
            "message": _message("system-message", "system", "private", "exchange-0", hidden=True),
            "parent": "root-node",
            "children": [],
        },
        {
            "id": "user-node",
            "message": _message("user-message", "user", "Synthetic question", "exchange-1"),
            "parent": "system-node",
            "children": [],
        },
        {
            "id": "empty-assistant-node",
            "message": _message("empty-assistant", "assistant", "", "exchange-1"),
            "parent": "user-node",
            "children": [],
        },
        {
            "id": "assistant-node",
            "message": _message(
                "assistant-message",
                "assistant",
                "Synthetic answer",
                "exchange-1",
            ),
            "parent": "empty-assistant-node",
            "children": [],
        },
    ]
    conversation = {
        "title": "Synthetic conversation",
        "conversation_id": "synthetic-conversation-id",
        "mapping": {node["id"]: node for node in nodes},
        "current_node": "assistant-node",
        "linear_conversation": nodes,
    }
    encoder = _FlatEncoder()
    encoder.encode({"loaderData": {"share": {"conversation": conversation}}})
    wire = json.dumps(encoder.values, separators=(",", ":")) + "\n"
    script = (
        "window.__reactRouterContext.streamController.enqueue("
        + json.dumps(wire)
        + ");"
    )
    return f"<!doctype html><html><script>{script}</script></html>".encode()


class SharedLinkSpikeTests(unittest.TestCase):
    def test_accepts_only_canonical_chatgpt_share_url(self) -> None:
        valid = "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"
        self.assertEqual(SPIKE.validate_share_url(valid), valid)
        invalid = (
            "http://chatgpt.com/share/00000000-0000-4000-8000-000000000000",
            "https://example.com/share/00000000-0000-4000-8000-000000000000",
            "https://chatgpt.com/share/../../private",
            "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000?next=x",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(SPIKE.SpikeError):
                SPIKE.validate_share_url(url)

    def test_extracts_only_visible_complete_nonempty_messages(self) -> None:
        body = _synthetic_page()
        parsed, timings = SPIKE.parse_share_page(body)
        self.assertEqual([message.role for message in parsed.messages], ["user", "assistant"])
        self.assertEqual(parsed.complete_turn_count, 1)
        self.assertEqual(parsed.nonstandard_exchange_count, 0)
        self.assertEqual(parsed.skipped_reasons["missing_message"], 1)
        self.assertEqual(parsed.skipped_reasons["visually_hidden"], 1)
        self.assertEqual(parsed.skipped_reasons["empty_text"], 1)
        self.assertGreaterEqual(timings["payload_decode_ms"], 0)

    def test_report_contains_no_transcript_or_native_identifiers(self) -> None:
        body = _synthetic_page()
        parsed, timings = SPIKE.parse_share_page(body)
        report = SPIKE.build_report(body, 1.0, parsed, timings)
        serialized = json.dumps(report)
        self.assertNotIn("Synthetic question", serialized)
        self.assertNotIn("Synthetic answer", serialized)
        self.assertNotIn("synthetic-conversation-id", serialized)
        self.assertTrue(report["canonical_v1_compatible"])


if __name__ == "__main__":
    unittest.main()
