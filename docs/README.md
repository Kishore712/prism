# Prism documentation

Start here, then go to whichever page fits what you're doing.

## For a user of Prism

- **[guides/local-test-walkthrough.md](guides/local-test-walkthrough.md)** —
  the full path, starting from a real ChatGPT conversation: capture, curate,
  publish, invite, serve, and read it back as a recipient. ~20 minutes.
- **[guides/cli-walkthrough.md](guides/cli-walkthrough.md)** — a compact
  command reference for the same flow, plus the Claude Code capture path.
- **[guides/recipient-guide.md](guides/recipient-guide.md)** — hand this to
  someone you've invited; it's everything they need and nothing about how
  Prism works internally.
- **[guides/owner-web-ui.md](guides/owner-web-ui.md)** — a simpler,
  form-based alternative to the CLI for the same owner workflow.

## For understanding how Prism is built

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the system design: problem
  statement, scope, terminology, component responsibilities, contracts, the
  HTTP/MCP interfaces, the Sealed Projection gate, security and privacy
  analysis, and persistence/lifecycle model.

## What's not here

Phase-by-phase delivery notes, the decision-by-decision design log, and
the literature survey behind the projection layer's design choices are kept
as internal working documents outside this repository — this folder is the
curated, current-state reference, not the running history of how it got
here.
