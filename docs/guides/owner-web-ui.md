# Owner web UI

A simple, server-rendered alternative to driving the CLI by hand. Same
workflow, same underlying services — capture, curate, review findings and
provenance, preview, publish, share, invite, approve grants, and audit —
just with forms and lists instead of copy-pasting IDs between commands.

```bash
PYTHONPATH=src .venv/bin/python -m prism ui --data-dir ~/prism-data
```

Then open `http://127.0.0.1:8788` (add `--port` to use a different one).

## "I don't see my drafts / snapshots / grants"

This is almost always **`--data-dir` pointing at the wrong place**, not a
broken page. Without `--data-dir`, it defaults to `./var` relative to
whatever directory you ran `prism ui` from (the same default every `prism`
CLI command uses, and the same footgun if you run it from a different
terminal or a different `cwd`) — so a UI started from a different place
than an earlier capture/publish silently opens a different, correctly-empty
database. `prism ui` prints the exact `prism.db` path it's reading from to
the terminal on startup; check that against wherever you ran `prism
capture` / `prism quickshare` / an earlier `prism ui`, and restart with the
same `--data-dir` (or `PRISM_DATA_DIR`) if they don't match. This is
deliberately terminal-only, not shown on the page itself — a filesystem
path is operator detail, not something a UI a recipient might glance over
your shoulder at should be printing.

## No login, on purpose

This page has no authentication. It binds to `127.0.0.1` only and has no
`--host`, `--tunnel`, or `--public-url` option — the same trust boundary the
`prism` CLI already relies on: whoever can run commands on this machine
already has full read/write access to the same local database. **Never**
put it behind a tunnel or expose it on a public host; use `prism mcp serve`
for the recipient-facing side instead, which does have real OAuth-backed
authorization. (This isn't printed on the page either, for the same reason
— it's the operator's responsibility, not a disclaimer to display to
whoever happens to be looking at the screen. It's documented here and in
the `prism ui` startup banner instead.)

## What you can do here

- **Capture** a thread from a ChatGPT shared link or a local Claude Code
  session file.
- **Curate** a draft: everything starts excluded; check the turns you want
  to share, attach text/Markdown files, optionally override the title.
- **Review findings** from the Sealed Projection gate and resolve or
  allow each one with a reason.
- **Review provenance**: see every captured tool-call event (Claude Code
  captures only), flag one as a mistake, and see the resulting findings
  and dependency-graph suggestions.
- **Preview** the exact recipient-visible snapshot, then **publish** it.
- **Share** a published snapshot, create one-time **invitations**, and
  **approve, deny, or revoke** grants as recipients redeem them.
- **Audit**: see who read what and when — metadata only, never content.

## What it doesn't do

It's a presentation layer, not a second implementation — every action here
calls the exact same service the CLI command does. If something is missing
here, it's missing from the CLI too (or it's a UI-only polish item; see the
project's backlog for what's tracked as follow-up work). It does not manage
`prism mcp serve` as a process — start that separately when you're ready to
actually expose a share to a recipient.
