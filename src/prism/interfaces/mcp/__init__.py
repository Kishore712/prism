"""Recipient-facing Model Context Protocol adapter."""

from .server import create_mcp_application, create_mcp_server

__all__ = ["create_mcp_application", "create_mcp_server"]
