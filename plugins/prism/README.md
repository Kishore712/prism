# Prism plugin package

Portable package metadata (`plugin.json`, Agent Plugins 1.0) plus an OpenAI
compatibility manifest (`.codex-plugin/plugin.json`, kept identical in content).

The package deliberately bundles **no active `mcp.json`**: each owner hosts their
own endpoint, so the URL is per share. `mcp.example.json` shows the shape.

## How a recipient connects

The endpoint is an OAuth 2.1 protected MCP server. The recipient adds the owner's
`https://…/mcp` URL and completes the standard authorization flow; the browser
step asks for the one-time invitation code the owner gave them. The owner then
approves the connection (`prism grant approve`), after which the tools work.

- **Claude Code:** `claude mcp add --transport http prism https://<owner-host>/mcp`,
  then `/mcp` inside Claude Code to authenticate.
- **ChatGPT:** add the URL as a custom connector / plugin in developer mode.
- **Any machine, no chat product:** `prism recipient connect <url> --headless`.

## Tools (all read-only)

`prism_get_manifest`, `prism_query_share`, `prism_read_message`,
`prism_read_resource`. No tool takes a grant, session, or snapshot identifier; the
grant comes from the verified OAuth token.
