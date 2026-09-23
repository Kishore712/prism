from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prism.exceptions import (
    ConversationNotFoundError,
    UnsupportedContentError,
    UnsupportedSourceFormatError,
)
from prism.models.capture import CaptureSelection, CaptureSource, MessageRole
from prism.providers.claude import ClaudeCodeSessionAdapter
from prism.services.normalization import CaptureNormalizer


def _user(text, **extra):
    return {"type": "user", "uuid": extra.pop("uuid", "u"), "message": {"role": "user", "content": text}, **extra}


def _assistant(*blocks, **extra):
    return {"type": "assistant", "uuid": extra.pop("uuid", "a"), "message": {"role": "assistant", "content": list(blocks)}, **extra}


def _text(value):
    return {"type": "text", "text": value}


def _write(path: Path, records) -> Path:
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    return path


SESSION = [
    {"type": "custom-title", "customTitle": "Plan the migration"},
    _user("How should we migrate?", uuid="u1"),
    _assistant({"type": "thinking", "thinking": "SECRET_THINKING"}, uuid="a1"),
    _assistant({"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat ~/.ssh/id_rsa"}}),
    {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "SECRET_TOOL_OUTPUT"}]}},
    _assistant(_text("Use a blue/green migration.")),
    _assistant(_text("Keep the old schema for one release.")),
    _user("<command-name>/clear</command-name>"),
    _user("caveat meta", isMeta=True),
    _user([_text("What about rollbacks?")], uuid="u2"),
    _assistant(_text("Roll back by flipping the switch."), uuid="a2"),
    _user("Do something with only tools", uuid="u3"),
    _assistant({"type": "tool_use", "id": "t2", "name": "Read", "input": {}}),
    _user("Sidechain question", isSidechain=True),
    _assistant(_text("Sidechain answer"), isSidechain=True),
    "not-a-record-at-all",
]


def _write_session(directory: str, records=SESSION, name="session-1.jsonl") -> Path:
    path = Path(directory) / name
    lines = [item if isinstance(item, str) else json.dumps(item) for item in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class ClaudeCodeAdapterTests(unittest.TestCase):
    def _session(self, directory: str, records=SESSION, name="session-1.jsonl") -> Path:
        return _write_session(directory, records=records, name=name)

    def test_captures_only_prompts_and_assistant_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._session(directory)
            adapter = ClaudeCodeSessionAdapter()
            inventory = adapter.inventory(CaptureSource(path=path))
            self.assertEqual(len(inventory.conversations), 1)
            summary = inventory.conversations[0]
            self.assertEqual(summary.title, "Plan the migration")
            self.assertEqual(summary.supported_message_count, 4)

            capture = adapter.capture(
                CaptureSource(path=path),
                CaptureSelection(conversation_ref=summary.conversation_ref),
            )
            self.assertEqual([m.role for m in capture.messages], [MessageRole.USER, MessageRole.ASSISTANT] * 2)
            self.assertEqual(capture.messages[0].content, "How should we migrate?")
            self.assertEqual(
                capture.messages[1].content,
                "Use a blue/green migration.\n\nKeep the old schema for one release.",
            )
            self.assertEqual(capture.messages[2].content, "What about rollbacks?")
            everything = json.dumps(capture.model_dump(mode="json"))
            for leaked in ("SECRET_THINKING", "SECRET_TOOL_OUTPUT", "id_rsa", "Sidechain", "caveat meta", "<command-name>"):
                self.assertNotIn(leaked, everything)
            codes = {warning.code for warning in capture.warnings}
            self.assertEqual(
                codes,
                {"tool_activity_omitted", "turns_without_text_dropped", "sidechain_omitted"},
            )
            # The normalizer accepts it as a canonical capture.
            canonical = CaptureNormalizer().normalize(adapter.version, capture)
            self.assertEqual(len(canonical.messages), 4)

    def test_directory_inventory_lists_sessions_and_skips_empty_ones(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._session(directory, name="a.jsonl")
            self._session(directory, records=[_user("hi")], name="empty.jsonl")
            adapter = ClaudeCodeSessionAdapter()
            inventory = adapter.inventory(CaptureSource(path=Path(directory)))
            self.assertEqual(len(inventory.conversations), 1)

    def test_refs_are_stable_and_unknown_refs_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._session(directory)
            adapter = ClaudeCodeSessionAdapter()
            first = adapter.inventory(CaptureSource(path=path)).conversations[0].conversation_ref
            second = adapter.inventory(CaptureSource(path=path)).conversations[0].conversation_ref
            self.assertEqual(first, second)
            with self.assertRaises(ConversationNotFoundError):
                adapter.capture(
                    CaptureSource(path=path),
                    CaptureSelection(conversation_ref="cc_" + "0" * 24),
                )

    def test_rejects_wrong_files_and_sessions_without_answers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = ClaudeCodeSessionAdapter()
            text = Path(directory) / "notes.txt"
            text.write_text("hello")
            with self.assertRaises(UnsupportedSourceFormatError):
                adapter.inventory(CaptureSource(path=text))
            with self.assertRaises(UnsupportedSourceFormatError):
                adapter.inventory(CaptureSource(path=Path(directory) / "missing.jsonl"))
            empty = self._session(directory, records=[_user("only a question")], name="q.jsonl")
            with self.assertRaises(UnsupportedContentError):
                adapter.inventory(CaptureSource(path=empty))


class ClaudeCodeProvenanceTests(unittest.TestCase):
    """Tier 1 (ground-truth) provenance extraction — see provenance.py."""

    def _capture_ref(self, adapter, source):
        return adapter.inventory(source).conversations[0].conversation_ref

    def test_implements_the_provenance_protocol(self) -> None:
        from prism.protocols.provenance_source import ProvenanceCaptureAdapter

        self.assertIsInstance(ClaudeCodeSessionAdapter(), ProvenanceCaptureAdapter)

    def test_read_write_edit_events_are_extracted_with_turn_index(self) -> None:
        records = [
            _user("Look at the config", uuid="u1"),
            _assistant(
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/etc/app.conf"}},
                _text("Looked."),
                uuid="a1",
            ),
            _user("Now fix it", uuid="u2"),
            _assistant(
                {"type": "tool_use", "id": "t2", "name": "Edit",
                 "input": {"file_path": "/etc/app.conf", "old_string": "a", "new_string": "b"}},
                _text("Fixed."),
                uuid="a2",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory, records=records)
            adapter = ClaudeCodeSessionAdapter()
            source = CaptureSource(path=path)
            ref = self._capture_ref(adapter, source)
            events = adapter.provenance(source, CaptureSelection(conversation_ref=ref))
        self.assertEqual(
            [(e.turn_index, e.tool_name, e.resource_label) for e in events],
            [(1, "Read", "/etc/app.conf"), (2, "Edit", "/etc/app.conf")],
        )

    def test_dropped_tool_only_turn_does_not_misattribute_its_event(self) -> None:
        """A turn whose assistant reply is tool-only (no text) is dropped from
        `capture()`. Its tool event must be dropped with it, not silently
        reassigned to whichever turn ends up with that index next — this was
        a real bug caught before merge."""

        records = [
            _user("Read the wrong file by mistake", uuid="u1"),
            _assistant(
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/tmp/orphan.txt"}},
                uuid="a1",  # no text block -> this turn is dropped entirely
            ),
            _user("What does the real doc say?", uuid="u2"),
            _assistant(_text("It says hello."), uuid="a2"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory, records=records)
            adapter = ClaudeCodeSessionAdapter()
            source = CaptureSource(path=path)
            ref = self._capture_ref(adapter, source)
            events = adapter.provenance(source, CaptureSelection(conversation_ref=ref))
            capture = adapter.capture(source, CaptureSelection(conversation_ref=ref))
        self.assertEqual(events, ())  # the orphaned event must not survive
        self.assertEqual(len(capture.messages), 2)  # only the one real turn
        self.assertEqual(capture.messages[0].content, "What does the real doc say?")

    def test_bash_command_and_grep_pattern_are_recorded(self) -> None:
        records = [
            _user("run stuff", uuid="u1"),
            _assistant(
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat /etc/passwd"}},
                {"type": "tool_use", "id": "t2", "name": "Grep", "input": {"pattern": "TODO", "path": "."}},
                _text("done"),
                uuid="a1",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory, records=records)
            adapter = ClaudeCodeSessionAdapter()
            source = CaptureSource(path=path)
            ref = self._capture_ref(adapter, source)
            events = adapter.provenance(source, CaptureSelection(conversation_ref=ref))
        self.assertEqual(events[0].tool_name, "Bash")
        self.assertEqual(events[0].resource_label, "cat /etc/passwd")
        # Grep has both `path` and `pattern`; `path` wins per the extraction order.
        self.assertEqual(events[1].tool_name, "Grep")
        self.assertEqual(events[1].resource_label, ".")

    def test_non_provenance_tools_are_not_recorded(self) -> None:
        records = [
            _user("what's next", uuid="u1"),
            _assistant(
                {"type": "tool_use", "id": "t1", "name": "TodoWrite", "input": {"todos": []}},
                {"type": "tool_use", "id": "t2", "name": "WebFetch", "input": {"url": "https://example.com"}},
                _text("done"),
                uuid="a1",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory, records=records)
            adapter = ClaudeCodeSessionAdapter()
            source = CaptureSource(path=path)
            ref = self._capture_ref(adapter, source)
            events = adapter.provenance(source, CaptureSelection(conversation_ref=ref))
        self.assertEqual(events, ())

    def test_tool_use_with_no_identifiable_input_still_records_the_tool_name(self) -> None:
        records = [
            _user("read something", uuid="u1"),
            _assistant(
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                _text("done"),
                uuid="a1",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory, records=records)
            adapter = ClaudeCodeSessionAdapter()
            source = CaptureSource(path=path)
            ref = self._capture_ref(adapter, source)
            events = adapter.provenance(source, CaptureSelection(conversation_ref=ref))
        self.assertEqual(events[0].tool_name, "Read")
        self.assertEqual(events[0].resource_label, "(no identifiable resource)")

    def test_sidechain_tool_calls_are_never_recorded(self) -> None:
        records = [
            _user("main task", uuid="u1"),
            {
                "type": "assistant",
                "uuid": "sub1",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/sub/agent/file"}},
                ]},
            },
            _assistant(_text("main answer"), uuid="a1"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory, records=records)
            adapter = ClaudeCodeSessionAdapter()
            source = CaptureSource(path=path)
            ref = self._capture_ref(adapter, source)
            events = adapter.provenance(source, CaptureSelection(conversation_ref=ref))
        self.assertEqual(events, ())

    def test_unknown_conversation_ref_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory)
            adapter = ClaudeCodeSessionAdapter()
            with self.assertRaises(ConversationNotFoundError):
                adapter.provenance(
                    CaptureSource(path=path),
                    CaptureSelection(conversation_ref="cc_" + "0" * 24),
                )

    def test_provenance_events_never_appear_in_the_capture_output(self) -> None:
        """Structural guarantee: provenance is extracted on a fully separate
        pass and must never leak into anything the sharing pipeline touches."""

        records = [
            _user("go", uuid="u1"),
            _assistant(
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "/Users/kishore/NEVER_SHOULD_APPEAR.secret"}},
                _text("ok"),
                uuid="a1",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = _write_session(directory, records=records)
            adapter = ClaudeCodeSessionAdapter()
            source = CaptureSource(path=path)
            ref = self._capture_ref(adapter, source)
            capture = adapter.capture(source, CaptureSelection(conversation_ref=ref))
        self.assertNotIn("NEVER_SHOULD_APPEAR", capture.model_dump_json())


if __name__ == "__main__":
    unittest.main()
