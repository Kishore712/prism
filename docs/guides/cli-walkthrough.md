# End-to-end walkthrough

Goal: share an agent thread with someone on another machine, keeping out the
turns and files you do not want to share, with the recipient authenticated and
you in control.

`prism` below is `.venv/bin/prism` after `pip install -e .` (or
`PYTHONPATH=src .venv/bin/python -m prism`).

## Owner (your machine)

### The guided way

```bash
prism quickshare --serve --tunnel ngrok --approve-here                 # local Claude Code session
prism quickshare --chatgpt-link --serve --tunnel ngrok --approve-here  # ChatGPT shared link
```

For a full test plan that starts from a ChatGPT shared link, see [`local-test-walkthrough.md`](local-test-walkthrough.md).

It lists your recent Claude Code sessions, shows numbered turns (everything is
excluded until you include it), lets you attach text/Markdown files, prints the
exact snapshot for confirmation, publishes it, creates a share and a one-time
invitation, and starts the server behind an ngrok tunnel. It prints the MCP URL
and the invitation code.

### Step by step

```bash
# 1. Capture a thread (local Claude Code transcript; no public link, no model)
prism capture list  ~/.claude/projects/<project>/<session>.jsonl --adapter claude-code-session
prism capture import ~/.claude/projects/<project>/<session>.jsonl \
  --adapter claude-code-session --conversation-ref <ref>        # prints capture and draft IDs

# 2. Curate: remove turns and resources you do not want to share
prism draft show <draft>                                        # numbered turns, all excluded
prism draft select <draft> --turns 1,3-5                        # complete allowlist
prism draft resource add <draft> --file notes.md                # share a file's text
prism draft resource remove <draft> <attachment-id>             # changed your mind

# 3. Publish exactly what you reviewed (immutable snapshot)
#    Blocks if a detector finds a secret/PII/env-identifier pattern, or content
#    chronologically after a tool-read you flag as a mistake (see below); publishes otherwise.
prism draft findings <draft>                                    # review what the Sealed Projection gate found
prism draft resolve <draft> <finding-id> --reason "..."         # explicitly allow one, if it's a false alarm
prism draft title <draft> --set "Public-safe title"             # optional: override the source title
prism draft publish <draft> --yes                               # prints the snapshot, then publishes it
prism snapshot receipt <snapshot>                                # signed record of what produced it

# 3a. If the agent accidentally read the wrong file mid-session (Claude Code
#     captures only), catch content it caused later, without repeating it verbatim
prism draft provenance <draft>                                  # list captured tool-call (Read/Write/...) events
prism draft taint <draft> <event-id> --reason "wrong file"      # flags every turn at/after it as a finding
prism draft graph <draft>                                       # non-blocking suggestions: what else might depend on it
prism draft untaint <draft> <event-id>                          # reverse it

# 4. Share and invite
prism share create <snapshot>
prism invitation create <share> --recipient-hint "Alice"        # prints the one-time code

# 5. Expose it
prism mcp serve --tunnel ngrok --owner-label "Your Name"        # prints https://….ngrok-free.app/mcp
#   or: prism mcp serve --public-url https://your-host.example   (you run your own HTTPS)

# 6. When the recipient connects, approve them
prism mcp serve --approve-here …                                # prompts you in the server terminal
#   or in another terminal: prism grants watch
prism grants list                                               # shows approval=pending and their name
prism grant approve <grant-id>                                  # or: prism grant deny <grant-id>
prism mcp check <url>                                           # self-test local or tunnel URL

# 7. Watch and control
prism audit list                                                # who read what, when (never content)
prism grant revoke <grant-id>                                   # next call is denied
```

Send the recipient the **MCP URL** and the **invitation code** over different
channels. Approval is a separate check that the person who redeemed the code is
the person you meant.

## Recipient (another machine)

Any of these, all performing the same OAuth flow:

**Claude Code**

```bash
claude mcp add --transport http prism https://<owner-host>/mcp
# then run /mcp inside Claude Code and choose to authenticate
```

**ChatGPT:** add `https://<owner-host>/mcp` as a custom connector / plugin in
developer mode.

**Prism CLI (no chat product needed)**

```bash
prism recipient connect https://<owner-host>/mcp --headless --name "Alice"
#   prompts (hidden) for the invitation code, then waits for the owner to approve
prism recipient manifest https://<owner-host>/mcp
prism recipient query    https://<owner-host>/mcp "how are recipients authorized?"
prism recipient message  https://<owner-host>/mcp <message-id>
prism recipient resource https://<owner-host>/mcp <resource-id>
```

Without `--headless` the CLI opens a browser for the consent page instead.

In the browser step the recipient enters their name and the invitation code. The
page shows which application is asking and where the result will be sent.

## What the recipient can and cannot do

- Read only the snapshot you published, at the version they were granted.
- Every result is marked as untrusted, owner-authored data.
- No tool takes an identity, session, or snapshot argument.
- You can revoke a grant, a share version, a share, or the snapshot at any time.

## Verify the whole path locally

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.e2e.test_full_journey -v
```

runs the owner and a recipient as separate processes with separate state,
including a probe that text from an excluded turn and an excluded file never
reaches the recipient.

## Known limits of this prototype

- The Sealed Projection gate is deterministic pattern matching and
  chronological taint propagation, not a model and not a completeness
  guarantee — read the printed snapshot yourself before confirming
  publication. See `ARCHITECTURE.md` §9.6.1–9.6.2 for what it does and
  does not claim to catch.
- Tier 1 provenance taint (`draft taint`) requires a source with tool-call
  logging (Claude Code today); a ChatGPT capture only gets the advisory
  dependency-graph suggestions (`draft graph`).
- The database on your machine is not encrypted at rest.
- The tunnel provider terminates TLS and can observe recipient traffic.
- Live ChatGPT and Claude connector runs have not been performed; the recipient
  CLI and the test suite exercise the same protocol.
- Recipient identity is "the person you approved", not a verified upstream account.
