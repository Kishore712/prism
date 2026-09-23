from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from prism.database import PrismDatabase
from prism.interfaces.mcp import create_mcp_server


class McpServerContractTests(unittest.TestCase):
    def test_tool_surface_is_recipient_only_read_only_and_has_no_identity_arguments(
        self,
    ) -> None:
        async def inspect_tools():
            with tempfile.TemporaryDirectory() as directory:
                server = create_mcp_server(PrismDatabase(Path(directory) / "prism.db"))
                return server._lowlevel_server.instructions, await server.list_tools()

        instructions, tools = asyncio.run(inspect_tools())
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(
            set(by_name),
            {
                "prism_get_manifest",
                "prism_query_share",
                "prism_read_message",
                "prism_read_resource",
            },
        )
        self.assertIn("DATA", instructions)
        for tool in tools:
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.destructive_hint)
            self.assertFalse(tool.annotations.open_world_hint)
            self.assertIsNotNone(tool.output_schema)
        serialized = repr([tool.input_schema for tool in tools])
        for forbidden in (
            "grant",
            "session",
            "snapshot_id",
            "invitation",
            "token",
            "principal",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_every_result_schema_carries_the_untrusted_content_envelope(self) -> None:
        async def inspect_tools():
            with tempfile.TemporaryDirectory() as directory:
                server = create_mcp_server(PrismDatabase(Path(directory) / "prism.db"))
                return await server.list_tools()

        for tool in asyncio.run(inspect_tools()):
            self.assertIn("provenance", repr(tool.output_schema), tool.name)


if __name__ == "__main__":
    unittest.main()
