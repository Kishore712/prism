# Claude provider

`claude_code.py` implements `SourceCaptureAdapter` for a **local Claude Code
session transcript** (`~/.claude/projects/<project>/<session>.jsonl`).

- One turn = one real user prompt plus all assistant *text* before the next prompt.
- Tool calls, tool results, thinking blocks, meta/system records, and sidechain
  (sub-agent) activity are never captured; their volume is reported as an
  informational warning.
- No network access, public link, or model is involved, so nothing about the
  session is exposed before the owner reviews the exact text.
- The transcript layout is a Claude Code implementation detail, not a public
  API: unknown shapes are skipped, and the owner reviews the exact preview.

Use it with `prism capture list|import <file-or-dir> --adapter claude-code-session`.
Claude.ai (web) conversations are not covered; that needs its own adapter.
