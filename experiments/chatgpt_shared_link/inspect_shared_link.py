"""Read-only compatibility spike for public ChatGPT shared links.

This module is deliberately outside ``src/prism``. It measures the current
provider page shape without making that undocumented shape a supported Prism
runtime contract. It never writes the response body or prints transcript text.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_PAGE_BYTES = 8 * 1024 * 1024
SHARE_PATH = re.compile(r"^/share/[A-Za-z0-9_-]{20,200}/?$")
ENQUEUE_CALL = "window.__reactRouterContext.streamController.enqueue("
CONVERSATION_KEYS = {
    "conversation_id",
    "current_node",
    "linear_conversation",
    "mapping",
    "title",
}


class SpikeError(RuntimeError):
    """Safe, user-facing error raised by the compatibility spike."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._in_script = False
        self._buffer: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() == "script":
            self._in_script = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_script:
            self.scripts.append("".join(self._buffer))
            self._in_script = False
            self._buffer = []


@dataclass(frozen=True)
class ParsedMessage:
    source_message_id: str
    exchange_ref: str
    role: str
    content: str


@dataclass(frozen=True)
class ParsedShare:
    title: str
    conversation_ref: str
    messages: tuple[ParsedMessage, ...]
    linear_node_count: int
    flat_item_count: int
    raw_roles: dict[str, int]
    raw_content_types: dict[str, int]
    skipped_reasons: dict[str, int]
    content_reference_types: dict[str, int]
    inline_citation_marker_count: int
    complete_turn_count: int
    nonstandard_exchange_count: int
    structure_fingerprint: str
    content_fingerprint: str


class _FlatIndexView:
    """Minimal reader for the flattened React Router payload observed in the spike."""

    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def value(self, reference: object) -> Any:
        if type(reference) is int and 0 <= reference < len(self.values):
            return self.values[reference]
        return None

    def key_name(self, encoded: object) -> object:
        if (
            isinstance(encoded, str)
            and encoded.startswith("_")
            and encoded[1:].isdigit()
        ):
            decoded = self.value(int(encoded[1:]))
            if isinstance(decoded, str):
                return decoded
        return encoded

    def keys(self, object_ref: object) -> tuple[str, ...]:
        value = self.value(object_ref)
        if not isinstance(value, dict):
            return ()
        return tuple(
            key
            for encoded in value
            if isinstance((key := self.key_name(encoded)), str)
        )

    def reference(self, object_ref: object, name: str) -> object | None:
        value = self.value(object_ref)
        if not isinstance(value, dict):
            return None
        for encoded, reference in value.items():
            if self.key_name(encoded) == name:
                return reference
        return None

    def field(self, object_ref: object, name: str) -> Any:
        return self.value(self.reference(object_ref, name))

    def find_conversation(self) -> int:
        matches = [
            index
            for index, value in enumerate(self.values)
            if isinstance(value, dict)
            and CONVERSATION_KEYS.issubset(self.keys(index))
        ]
        if len(matches) != 1:
            raise SpikeError(
                "Expected exactly one supported conversation object in the shared page"
            )
        return matches[0]


def validate_share_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "chatgpt.com"
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not SHARE_PATH.fullmatch(parsed.path)
    ):
        raise SpikeError("Only a canonical public ChatGPT shared-link URL is accepted")
    return raw_url


def fetch_share_page(raw_url: str, timeout_seconds: float = 30.0) -> tuple[bytes, float]:
    url = validate_share_url(raw_url)
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36 "
                "PrismCompatibilitySpike/0.1"
            ),
        },
        method="GET",
    )
    started = time.perf_counter()
    try:
        with build_opener(_NoRedirects).open(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise SpikeError(f"Shared page returned HTTP {response.status}")
            if response.headers.get_content_type() != "text/html":
                raise SpikeError("Shared page did not return HTML")
            body = response.read(MAX_PAGE_BYTES + 1)
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise SpikeError("Shared page attempted an unsupported redirect") from exc
        raise SpikeError(f"Shared page returned HTTP {exc.code}") from exc
    except (TimeoutError, URLError) as exc:
        raise SpikeError("Shared page could not be retrieved within the timeout") from exc
    if len(body) > MAX_PAGE_BYTES:
        raise SpikeError("Shared page exceeded the spike's response-size limit")
    return body, (time.perf_counter() - started) * 1000


def _decode_flat_payload(html_text: str) -> list[Any]:
    collector = _ScriptCollector()
    collector.feed(html_text)
    candidate_scripts = [
        script
        for script in collector.scripts
        if ENQUEUE_CALL in script and "conversation_id" in script
    ]
    if len(candidate_scripts) != 1:
        raise SpikeError("The shared page did not contain one recognized data stream")

    script = candidate_scripts[0]
    start = script.index(ENQUEUE_CALL) + len(ENQUEUE_CALL)
    try:
        wire_text, _ = json.JSONDecoder().raw_decode(script[start:])
    except json.JSONDecodeError as exc:
        raise SpikeError("The shared-page stream argument is not valid JSON") from exc
    if not isinstance(wire_text, str):
        raise SpikeError("The shared-page stream argument has an unsupported type")

    root_records = [line for line in wire_text.splitlines() if line.startswith("[")]
    if len(root_records) != 1:
        raise SpikeError("The shared-page stream has an unsupported record layout")
    try:
        flattened = json.loads(root_records[0])
    except json.JSONDecodeError as exc:
        raise SpikeError("The shared-page root record is not valid JSON") from exc
    if not isinstance(flattened, list):
        raise SpikeError("The shared-page root record is not a flattened list")
    return flattened


def _normalized_text(parts: object, view: _FlatIndexView) -> str | None:
    if not isinstance(parts, list):
        return None
    decoded: list[str] = []
    for part_ref in parts:
        part = view.value(part_ref)
        if not isinstance(part, str):
            return None
        decoded.append(part)
    text = unicodedata.normalize(
        "NFC",
        "\n".join(decoded).replace("\r\n", "\n").replace("\r", "\n"),
    )
    return text if text.strip() else ""


def parse_share_page(body: bytes) -> tuple[ParsedShare, dict[str, float]]:
    try:
        html_text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpikeError("Shared page is not valid UTF-8") from exc

    decode_started = time.perf_counter()
    flattened = _decode_flat_payload(html_text)
    decode_ms = (time.perf_counter() - decode_started) * 1000

    extraction_started = time.perf_counter()
    view = _FlatIndexView(flattened)
    conversation_ref = view.find_conversation()
    title = view.field(conversation_ref, "title")
    native_conversation_id = view.field(conversation_ref, "conversation_id")
    nodes = view.field(conversation_ref, "linear_conversation")
    if not isinstance(title, str) or not title.strip():
        raise SpikeError("The shared conversation has no usable title")
    if not isinstance(native_conversation_id, str) or not native_conversation_id:
        raise SpikeError("The shared conversation has no stable identifier")
    if not isinstance(nodes, list) or not nodes:
        raise SpikeError("The shared conversation has no linear message path")

    raw_roles: Counter[str] = Counter()
    raw_content_types: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    content_reference_types: Counter[str] = Counter()
    selected: list[ParsedMessage] = []
    schemas: set[tuple[str, ...]] = set()
    inline_citations = 0

    for node_ref in nodes:
        message_ref = view.reference(node_ref, "message")
        if (
            type(message_ref) is not int
            or message_ref < 0
            or not isinstance(view.value(message_ref), dict)
        ):
            skipped["missing_message"] += 1
            continue
        schemas.add(tuple(sorted(view.keys(message_ref))))
        author_ref = view.reference(message_ref, "author")
        content_ref = view.reference(message_ref, "content")
        metadata_ref = view.reference(message_ref, "metadata")
        role = view.field(author_ref, "role")
        content_type = view.field(content_ref, "content_type")
        status = view.field(message_ref, "status")
        raw_roles[str(role)] += 1
        raw_content_types[str(content_type)] += 1

        if view.field(metadata_ref, "is_visually_hidden_from_conversation") is True:
            skipped["visually_hidden"] += 1
            continue
        if view.field(metadata_ref, "is_redacted") is True:
            skipped["redacted"] += 1
            continue
        if role not in {"user", "assistant"}:
            skipped["unsupported_role"] += 1
            continue
        if status != "finished_successfully":
            skipped["incomplete"] += 1
            continue
        if content_type != "text":
            skipped["unsupported_content_type"] += 1
            continue

        text = _normalized_text(view.field(content_ref, "parts"), view)
        if text is None:
            skipped["unsupported_text_parts"] += 1
            continue
        if not text:
            skipped["empty_text"] += 1
            continue

        source_message_id = view.field(message_ref, "id")
        exchange_ref = (
            view.field(metadata_ref, "turn_exchange_id")
            or view.field(metadata_ref, "working_turn_id")
        )
        if not isinstance(source_message_id, str) or not source_message_id:
            raise SpikeError("A selected message has no stable source identifier")
        if not isinstance(exchange_ref, str) or not exchange_ref:
            raise SpikeError("A selected message has no stable exchange identifier")

        references = view.field(metadata_ref, "content_references")
        if isinstance(references, list):
            for reference_ref in references:
                reference_type = view.field(reference_ref, "type")
                content_reference_types[str(reference_type)] += 1
        inline_citations += text.count("\ue200cite\ue202")
        selected.append(
            ParsedMessage(
                source_message_id=source_message_id,
                exchange_ref=exchange_ref,
                role=role,
                content=text,
            )
        )

    exchanges: OrderedDict[str, list[str]] = OrderedDict()
    for message in selected:
        exchanges.setdefault(message.exchange_ref, []).append(message.role)
    complete_turns = sum(roles == ["user", "assistant"] for roles in exchanges.values())
    nonstandard_exchanges = len(exchanges) - complete_turns

    structure_bytes = json.dumps(
        {
            "conversation_keys": sorted(view.keys(conversation_ref)),
            "message_schemas": sorted(schemas),
            "raw_content_types": dict(sorted(raw_content_types.items())),
            "raw_roles": dict(sorted(raw_roles.items())),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    content_bytes = json.dumps(
        {
            "messages": [
                {"content": message.content, "role": message.role}
                for message in selected
            ],
            "title": title,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    parsed = ParsedShare(
        title=title,
        conversation_ref=native_conversation_id,
        messages=tuple(selected),
        linear_node_count=len(nodes),
        flat_item_count=len(flattened),
        raw_roles=dict(sorted(raw_roles.items())),
        raw_content_types=dict(sorted(raw_content_types.items())),
        skipped_reasons=dict(sorted(skipped.items())),
        content_reference_types=dict(sorted(content_reference_types.items())),
        inline_citation_marker_count=inline_citations,
        complete_turn_count=complete_turns,
        nonstandard_exchange_count=nonstandard_exchanges,
        structure_fingerprint=f"sha256:{hashlib.sha256(structure_bytes).hexdigest()}",
        content_fingerprint=f"sha256:{hashlib.sha256(content_bytes).hexdigest()}",
    )
    extraction_ms = (time.perf_counter() - extraction_started) * 1000
    return parsed, {
        "payload_decode_ms": round(decode_ms, 3),
        "message_extraction_ms": round(extraction_ms, 3),
    }


def build_report(
    body: bytes,
    fetch_ms: float,
    parsed: ParsedShare,
    timings: dict[str, float],
) -> dict[str, object]:
    text_characters = sum(len(message.content) for message in parsed.messages)
    maximum_message_characters = max(len(message.content) for message in parsed.messages)
    return {
        "source": "chatgpt_shared_link",
        "observed_format": "react_router_flat_stream_v1",
        "page_bytes": len(body),
        "flat_item_count": parsed.flat_item_count,
        "linear_node_count": parsed.linear_node_count,
        "selected_message_count": len(parsed.messages),
        "complete_turn_count": parsed.complete_turn_count,
        "nonstandard_exchange_count": parsed.nonstandard_exchange_count,
        "canonical_v1_compatible": parsed.nonstandard_exchange_count == 0,
        "selected_text_characters": text_characters,
        "maximum_message_characters": maximum_message_characters,
        "title_characters": len(parsed.title),
        "raw_roles": parsed.raw_roles,
        "raw_content_types": parsed.raw_content_types,
        "skipped_reasons": parsed.skipped_reasons,
        "content_reference_types": parsed.content_reference_types,
        "inline_citation_marker_count": parsed.inline_citation_marker_count,
        "structure_fingerprint": parsed.structure_fingerprint,
        "content_fingerprint": parsed.content_fingerprint,
        "timing_ms": {
            "fetch": round(fetch_ms, 3),
            **timings,
        },
        "privacy": {
            "response_persisted": False,
            "transcript_printed": False,
            "cookies_supplied": False,
            "redirects_allowed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a public ChatGPT shared link without printing its transcript."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Optional for automation; omit it to use the non-echoing prompt.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    try:
        raw_url = args.url or getpass.getpass("ChatGPT shared link: ")
        body, fetch_ms = fetch_share_page(raw_url, timeout_seconds=args.timeout)
        parsed, timings = parse_share_page(body)
        print(json.dumps(build_report(body, fetch_ms, parsed, timings), indent=2))
    except SpikeError as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
