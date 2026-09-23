# Prism

Prism is a research project exploring how stateful AI agents and their associated project context can be shared safely and effectively between people.

## Status

**A first end-to-end prototype works.** An owner captures an agent thread, removes the turns and files they do not want to share, publishes an immutable snapshot, and exposes it over HTTPS. A recipient on another machine authenticates with OAuth (one-time invitation plus owner approval) and reads it from Claude, ChatGPT, or the bundled CLI. The full path is exercised by a cross-process test.

**Publication runs through a Sealed Projection gate** (`prism.projection`, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §9.6.1): deterministic secret/PII/environment-identifier detectors block publication until the owner explicitly reviews and allows each finding (`prism draft findings` / `draft resolve`), a post-condition rescan re-checks the built snapshot bytes independently, the title is owner-editable rather than always copied in, and each snapshot gets a signed, verifiable receipt (`prism snapshot receipt`). It also catches content derived from a tool call that accidentally read the wrong file, once the owner flags the read (`prism draft taint`, §9.6.2).

**A simple owner web UI is now available** (`prism ui`, loopback-only, no login) as a form-based alternative to the CLI over the same workflow — see [`docs/guides/owner-web-ui.md`](docs/guides/owner-web-ui.md).

Not yet done, and the focus of the next iterations: encryption at rest, verified upstream identity, and live runs against real ChatGPT and Claude connectors.

**Test it yourself, starting from a ChatGPT shared link:** [`docs/guides/local-test-walkthrough.md`](docs/guides/local-test-walkthrough.md). Command reference for the whole flow: [`docs/guides/cli-walkthrough.md`](docs/guides/cli-walkthrough.md). The architecture reference is [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); start from [`docs/README.md`](docs/README.md) for the full documentation index.

## Quick start

```bash
.venv/bin/python -m pip install -e .

# Owner: guided, from a ChatGPT shared link (or a local Claude Code session) to an MCP URL and invitation code
prism quickshare --chatgpt-link            # or: prism quickshare  (Claude Code session)
prism mcp serve --approve-here --tunnel ngrok   # approve recipients right in this terminal
prism mcp check <url>                      # self-test the URL before anyone connects

# Recipient (any machine)
prism recipient connect https://<owner-host>/mcp --headless --name "Alice"
prism recipient query   https://<owner-host>/mcp "what was decided about access control?"

# Owner approves the connection when it appears (or: prism grants watch)

# Prefer forms to commands? A loopback-only, unauthenticated owner UI:
prism ui --port 8788
```

## Prototype phases

Each phase was tested and reviewed before the next phase began. Phases 1
through 10 are implemented; see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the component-by-component design rather than this phase history.

1. **Phase 1 — JSON inspection scaffold.** Deterministic source inspection with synthetic fixtures.
2. **Phase 2 — canonical capture and ChatGPT export import.** Safely import one owner-selected ChatGPT conversation into an owner-only canonical capture. The synthetic sample path is implemented; compatibility with a real export remains gated on sanitized regression data.
3. **Phase 3 — ChatGPT shared-link capture.** Fetch one owner-created public snapshot without credentials or script execution, show the normalized candidate, and persist it only after confirmation.
4. **Phase 4 — selection, preview, and durable owner state.** Complete-turn allowlisting, exact preview, and SQLite as the authoritative store for candidates, captures, resumable drafts, and preview revisions.
5. **Phase 5 — database-backed publication, shares, and grants.** Immutable deduplicated snapshots, stable share versions, one-time invitations, expiry, token hashing, and cascading revocation.
6. **Phase 6 — Recipient MCP tools.** Bounded read-only tools over one authorized share (its session-based authorization was replaced in Phase 7).
7. **Phase 7 — Identity-bound OAuth grants and the end-to-end flow.** Owner-hosted OAuth 2.1, browser invitation redemption, owner approval, stateless MCP, owner-attached resources, Claude Code capture, `quickshare`, recipient CLI, tunnel helper.
8. **Phase 8 — Sealed Projection gate.** Deterministic secret/PII/environment-identifier detectors, an exclusion-derived check, a per-draft decision ledger, a release-gate pre-check and post-condition rescan, an owner-editable title, and signed receipts.
9. **Phase 9 — Provenance-aware taint propagation.** Ground-truth tool-call provenance from Claude Code's own log (`draft taint`), and a unified lexical/cue-phrase/provenance dependency graph (`draft graph`) as a non-blocking suggestion layer.
10. **Phase 10 — Owner web UI.** A server-rendered, loopback-only, unauthenticated UI (`prism ui`) over the same owner workflow, so driving Prism doesn't require the CLI.
11. **Next iterations.** Encryption at rest, verified upstream (OIDC) identity, a hosted relay, live connector verification, additional platforms.

## Foundational Phase 1 scaffold

The original scaffold remains as regression coverage alongside the later phases:

- `SyntheticSessionSource` reads Prism's versioned synthetic session, validates it, removes incomplete turns, resolves session-associated resources, and produces a normalized snapshot.
- `inspect-session` presents that snapshot to the owner. It is read-only and does not decide what will eventually be shared.

The synthetic session source is a development component, not a ChatGPT provider. It exercises the same provider-neutral capture protocols without claiming compatibility with an external platform.

## Run the current scaffold

The repository already uses a local virtual environment. Install the declared runtime dependencies when needed:

```bash
cd /Users/kishore/Projects/prism
.venv/bin/python -m pip install -e .
```

Run the Phase 1 scaffold:

```bash
cd /Users/kishore/Projects/prism
PYTHONPATH=src .venv/bin/python -m prism \
  inspect-session examples/sessions/synthetic_session.json
```

To inspect the normalized machine-readable result:

```bash
PYTHONPATH=src .venv/bin/python -m prism \
  inspect-session examples/sessions/synthetic_session.json --json
```

Run the tests:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected scaffold behavior:

- `turn-1` and `turn-2` are listed.
- `turn-3` is omitted because its assistant response is still in progress.
- Both associated resources are visible to the owner for later selection.
- Nothing is published or made available to a recipient.

## Try the Phase 2 selection flow

The commands below use the repository source directly, so an editable package installation is not required. Confirm that Pydantic v2 is available in the existing virtual environment:

```bash
cd /Users/kishore/Projects/prism
.venv/bin/python -c "import pydantic; print(pydantic.__version__)"
```

If that import fails, install all declared runtime dependencies:

```bash
.venv/bin/python -m pip install -e .
```

List the available conversations without persisting message content:

```bash
PYTHONPATH=src .venv/bin/python -m prism \
  capture list examples/chatgpt/synthetic_export.json
```

Experience the numbered owner-selection flow and choose conversation `1`:

```bash
PYTHONPATH=src .venv/bin/python -m prism \
  capture select examples/chatgpt/synthetic_export.json
```

The current implementation writes the canonical capture and an excluded-by-default durable draft to `./var/prism.db` in one transaction. The result prints both `capture_id` and `draft_id`. The `var/` directory is ignored by Git.

For a scriptable two-step flow, first request JSON inventory output:

```bash
PYTHONPATH=src .venv/bin/python -m prism \
  capture list examples/chatgpt/synthetic_export.json --json
```

Copy one `conversation_ref` from that result and import it explicitly:

```bash
PYTHONPATH=src .venv/bin/python -m prism \
  capture import examples/chatgpt/synthetic_export.json \
  --conversation-ref <conversation_ref> \
  --json
```

The example contains a selected branch, an unrelated synthetic private conversation, and a deliberately unsupported multimodal conversation. The importer persists only the conversation selected by the owner. [`examples/chatgpt/synthetic_export.json`](examples/chatgpt/synthetic_export.json) is a Prism-owned imitation for local development; it is not an official or guaranteed ChatGPT export schema.

## Try shared-link capture and snapshot preview

Create and manually review a public ChatGPT shared link, then run:

```bash
cd /Users/kishore/Projects/prism
PYTHONPATH=src .venv/bin/python -m prism shared-link review
```

Prism asks for the link using a visible terminal prompt. Because the URL is entered after the process starts rather than as a command argument, it does not enter shell history. The local flow then:

1. fetches the public page without ChatGPT cookies or credentials;
2. parses the embedded structured data without executing provider JavaScript;
3. shows candidate counts, warnings, and numbered turns;
4. optionally shows exact normalized text for chosen candidate turns or `all`;
5. stores the normalized candidate temporarily in `prism.db`, without the native URL or raw page, so it can be resumed by `import_id`;
6. atomically creates the complete owner-only capture and excluded-by-default durable draft after confirmation;
7. saves a turn allowlist such as `1,3-5` or `all` as a new draft revision; and
8. prints exact recipient-visible snapshot JSON and records its deterministic hash against that revision.

This command does not persist a published snapshot, create a recipient invitation, open a tunnel, or start an MCP server. Revoke the native ChatGPT shared link separately in ChatGPT when it is no longer needed.

Visible input is the default. The explicit form remains available:

```bash
PYTHONPATH=src .venv/bin/python -m prism shared-link review --visible-input
```

Visible input keeps the URL out of shell history, but it can remain visible in terminal scrollback. Paste only the raw `https://chatgpt.com/share/...` URL, not Markdown such as `[label](URL)`. To hide the URL instead, use `--hidden-input`; paste with `Command+V`, press Enter, and Prism will report only the received character count.

## Resume durable state

If Prism exits after staging a shared-link candidate, resume it using the printed import ID:

```bash
PYTHONPATH=src .venv/bin/python -m prism \
  shared-link resume <import-id>
```

Inspect and update remembered draft selections without recapturing the conversation:

```bash
PYTHONPATH=src .venv/bin/python -m prism drafts list
PYTHONPATH=src .venv/bin/python -m prism draft show <draft-id>
PYTHONPATH=src .venv/bin/python -m prism draft select <draft-id> --turns 1,3-5
PYTHONPATH=src .venv/bin/python -m prism draft preview <draft-id>
```

Database administration commands are local-only:

```bash
PYTHONPATH=src .venv/bin/python -m prism db init
PYTHONPATH=src .venv/bin/python -m prism db check
PYTHONPATH=src .venv/bin/python -m prism db backup
PYTHONPATH=src .venv/bin/python -m prism db migrate-legacy
```

`migrate-legacy` validates and imports `var/captures/*/capture.json` idempotently. It never deletes the source JSON.

## Publish and test Phase 5 locally

After `draft preview`, copy the printed revision and hash and publish exactly that state:

```bash
PYTHONPATH=src .venv/bin/python -m prism draft publish <draft-id> \
  --expected-revision <revision> \
  --expected-preview-hash <sha256-hash>
```

Create a stable share and one-time invitation from the returned snapshot ID:

```bash
PYTHONPATH=src .venv/bin/python -m prism share create <snapshot-id>
PYTHONPATH=src .venv/bin/python -m prism invitation create <share-id>
```

Redeem and verify capabilities using the default hidden prompts:

```bash
PYTHONPATH=src .venv/bin/python -m prism invitation redeem
PYTHONPATH=src .venv/bin/python -m prism grant verify
```

The raw invitation and grant values are displayed once; SQLite stores only domain-separated hashes and short hints. The optional `--token` argument is for automated testing and can place a secret in shell history.

Useful lifecycle commands:

```bash
PYTHONPATH=src .venv/bin/python -m prism snapshots list
PYTHONPATH=src .venv/bin/python -m prism shares list
PYTHONPATH=src .venv/bin/python -m prism invitations list
PYTHONPATH=src .venv/bin/python -m prism grants list
PYTHONPATH=src .venv/bin/python -m prism grant revoke <grant-id>
PYTHONPATH=src .venv/bin/python -m prism share revoke-version <share-id> --version <N>
PYTHONPATH=src .venv/bin/python -m prism share revoke <share-id>
PYTHONPATH=src .venv/bin/python -m prism snapshot revoke <snapshot-id>
```

Add `--purge` to `snapshot revoke` only when the owner also intends to delete the immutable payload bytes. Prism retains a tombstone and revokes all dependent access. This does not revoke the original ChatGPT public link.

## Source layout

```text
src/prism/
├── database/        # SQLAlchemy engine, rows, unit of work, and Alembic migrations
├── repositories/    # Session-bound capture, draft, snapshot, share, and grant repositories
├── models/          # Provider-neutral Pydantic contracts
├── protocols/       # Structural interfaces implemented by adapters and stores
├── services/        # Capture, draft, projection, publication, sharing, and administration workflows
├── providers/
│   ├── chatgpt/     # ChatGPT export and shared-link acquisition
│   ├── claude/      # Reserved Claude provider boundary
│   └── registry.py  # Explicitly enabled provider adapters
├── archives/        # Bounded, provider-neutral archive reading
├── storage/         # SQLite production store and legacy JSON store
├── development/     # Synthetic sources used only for development and tests
├── cli.py           # Command-line entry point and dependency assembly
├── config.py        # Environment-backed runtime configuration
├── exceptions.py    # Stable Prism error types
└── __main__.py
```

Provider packages may parse their own source formats, but they must return the contracts defined by `SourceCaptureAdapter`. Provider-specific source structures must not leak into `models`, `services`, or recipient-facing contracts. Human-runnable synthetic inputs live under `examples`; future regression-only inputs belong under `tests/data`. Executable synthetic sources belong under `development`, not `providers`.

## Collaboration

- Use a separate branch for each change.
- Open a pull request before merging into `main`.
- Keep unfinished notes and local artifacts out of the repository.
- Do not commit credentials, private conversations, personal data, or restricted research material.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the working conventions.

## License

No license has been selected yet. Until one is added, the project should be treated as private and all rights reserved.
