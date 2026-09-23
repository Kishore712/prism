from __future__ import annotations

import json
from typing import Any


class FlatEncoder:
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


def message(
    message_id: str,
    role: str,
    text: str,
    exchange_id: str,
    *,
    hidden: bool = False,
    references: list[dict[str, str]] | None = None,
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
            "content_references": references or [],
        },
    }


def synthetic_shared_page() -> bytes:
    nodes = [
        {"id": "root-node", "message": None, "parent": None, "children": []},
        {
            "id": "system-node",
            "message": message(
                "native-system-message",
                "system",
                "PRIVATE SYSTEM TEXT",
                "native-exchange-0",
                hidden=True,
            ),
            "parent": "root-node",
            "children": [],
        },
        {
            "id": "user-node-1",
            "message": message(
                "native-user-message-1",
                "user",
                "First question",
                "native-exchange-1",
            ),
            "parent": "system-node",
            "children": [],
        },
        {
            "id": "assistant-node-1",
            "message": message(
                "native-assistant-message-1",
                "assistant",
                "First answer",
                "native-exchange-1",
                references=[{"type": "grouped_webpages"}],
            ),
            "parent": "user-node-1",
            "children": [],
        },
        {
            "id": "user-node-2",
            "message": message(
                "native-user-message-2",
                "user",
                "Second question",
                "native-exchange-2",
            ),
            "parent": "assistant-node-1",
            "children": [],
        },
        {
            "id": "assistant-node-2",
            "message": message(
                "native-assistant-message-2",
                "assistant",
                "Second answer",
                "native-exchange-2",
            ),
            "parent": "user-node-2",
            "children": [],
        },
    ]
    conversation = {
        "title": "Synthetic shared conversation",
        "conversation_id": "native-conversation-id",
        "mapping": {node["id"]: node for node in nodes},
        "current_node": "assistant-node-2",
        "linear_conversation": nodes,
    }
    encoder = FlatEncoder()
    encoder.encode({"loaderData": {"share": {"conversation": conversation}}})
    wire = json.dumps(encoder.values, separators=(",", ":")) + "\n"
    script = (
        "window.__reactRouterContext.streamController.enqueue("
        + json.dumps(wire)
        + ");"
    )
    return f"<!doctype html><html><script>{script}</script></html>".encode()


class StaticFetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.received_url: str | None = None

    def fetch(self, raw_url: str) -> bytes:
        self.received_url = raw_url
        return self.body
