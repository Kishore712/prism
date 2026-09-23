# Test Prism locally, starting from a ChatGPT shared link

This walks you from a real ChatGPT conversation to a recipient reading a
curated snapshot of it, all on your machine, then optionally from Claude Code,
ChatGPT, or a second machine. It takes about 20 minutes.

```
ChatGPT conversation ──share link──▶ prism quickshare ──▶ snapshot ──▶ prism mcp serve
   (you, in chatgpt.com)              (capture, pick turns,   (immutable)      │  OAuth + your approval
                                       attach files, publish)                   ▼
                                                              recipient: CLI · Claude Code · ChatGPT · other machine
```

What you will prove: the recipient sees **only** what you chose, must be
**approved by you**, can be **cut off instantly**, and you can see **who read
what** (never the content).

## 0. Before you start

```bash
cd ~/Projects/prism
.venv/bin/python -m pip install -e .          # provides the `prism` command
source .venv/bin/activate                     # so `prism` is on your PATH
prism --help | head -3

# Use a throwaway data directory so nothing mixes with earlier experiments
export PRISM_DATA_DIR="$HOME/prism-test-data"

# Optional but recommended: confirm the code itself is healthy first
PYTHONPATH=src python -m unittest discover -s tests 2>&1 | tail -3      # expect: OK
```

You need: this repo's virtualenv, a ChatGPT account, and for the optional parts
`ngrok` (already installed at `/opt/homebrew/bin/ngrok`, needs `ngrok config
add-authtoken …` once) and Claude Code.

## 1. Prepare a ChatGPT conversation worth testing

Use a **text-only** conversation of 4 to 8 turns. The capture is deliberately
strict: it refuses conversations with content it cannot represent faithfully
(images, uploaded files, tool or code-interpreter output, unfinished replies)
instead of guessing.

Make one of the turns your **canary**: a turn containing a fake secret you will
exclude, for example ask *"Remember that our internal project code name is
BLUEHERON-4471"*. If `BLUEHERON-4471` ever reaches the recipient, exclusion is
broken. Also make a small file on disk you will **not** attach, and one you will:

```bash
mkdir -p ~/prism-test-files
echo "PRIVATE_FILE_CANARY_9d2e - do not share" > ~/prism-test-files/private.txt
printf '# Shared notes\nWe decided recipients authenticate with OAuth.\n' > ~/prism-test-files/notes.md
```

Create the link in ChatGPT: open the conversation, use **Share**, and choose
**Create link**. Copy the `https://chatgpt.com/share/...` URL.

> The link is a **public bearer URL**: anyone who has it can read the whole
> conversation until you delete it. Prism reads it once, without your cookies or
> credentials and without running any of the page's scripts. You will delete it
> in step 3.

## 2. Capture, choose, publish, invite (one guided command)

```bash
prism quickshare --chatgpt-link --recipient-hint "Alice"
```

It asks, in order (answers shown after the arrow):

| Prompt | Your answer |
|---|---|
| `ChatGPT shared link (URL only; input visible)` | paste the URL |
| shows the candidate turns and any skipped provider records, then `Store this complete conversation on this machine …?` | `y` (add `--hidden-input` if you do not want the URL echoed) |
| lists numbered turns, all **excluded** | read them |
| `Turns to share (e.g. 1,3-5, all, none)` | every turn number **except** the canary turn, e.g. `1,2,4,5` |
| `Attach a text/Markdown file to share` | `~/prism-test-files/notes.md`, then press Enter on the next prompt to finish (do **not** attach `private.txt`) |
| prints `Exact recipient-visible snapshot:` | **read it carefully.** Check the canary is absent. This is what the recipient will see. |
| `Publish exactly this to a new immutable snapshot? [y/N]` | `y` |

If the canary looks like a real secret pattern (an AWS-style key, an email, etc.), publication stops here with an error naming a finding instead of asking you to confirm — that's the **Sealed Projection gate** (see `ARCHITECTURE.md` §9.6.1). Review and explicitly allow it, with a reason, then publish again from the same draft:

```bash
prism draft findings <draft-id>
prism draft resolve <draft-id> <finding-id> --reason "why this is safe to share"
prism draft publish <draft-id> --yes
```

Note the two things it prints and remember them:

- `Invitation code (…shown once): pinv_v1_…` , the one-time code.
- The reminder to delete the ChatGPT link.

**Now delete the ChatGPT shared link** (ChatGPT → Settings → Data controls →
Shared links → manage/delete; menu names change). Prism already has its own
owner-only copy.

Optional inspection: `prism drafts list`, `prism snapshots list`, `prism shares list`.

## 3. Start the server (local first)

Open a **second terminal** (activate the venv and `export PRISM_DATA_DIR` again):

```bash
prism mcp serve --owner-label "Your Name" --approve-here
# prints: MCP endpoint for the recipient: http://127.0.0.1:8765/mcp
```

`--approve-here` makes this terminal ask you to approve each recipient the
moment they redeem their invitation. In a third terminal, self-check it:

```bash
prism mcp check http://127.0.0.1:8765/mcp
# expect five [ok] lines and "All checks passed."
```

(The default port is 8765; add `--port` to change it.)

## 4. Be the recipient

### A. Same machine, with the Prism CLI (fastest)

Use a **separate state directory** to behave like another machine:

```bash
prism recipient connect http://127.0.0.1:8765/mcp --headless --name "Alice" \
  --state-dir ~/prism-recipient-a
# prompts (hidden) for the invitation code from step 2, then:
#   Waiting for the owner to approve this connection…
```

Switch to the server terminal. You will see something like:

```
>>> 'Alice' redeemed an invitation you made for 'Alice' (grant grt_…)
    Is this the person you meant? Approve? [y/N]
```

Type `y`. The recipient terminal finishes and prints the manifest. Then:

```bash
U=http://127.0.0.1:8765/mcp ; D="--state-dir $HOME/prism-recipient-a"
prism recipient manifest $U $D
prism recipient query    $U "what was decided?" $D
prism recipient message  $U <a-message-id-from-the-manifest> $D
prism recipient resource $U <the-resource-id-for-notes.md> $D
```

### B. Claude Code as the recipient (local)

```bash
claude mcp add --transport http prism-share http://127.0.0.1:8765/mcp
claude            # then type /mcp, pick prism-share, choose Authenticate
```

Your browser opens Prism's consent page. Enter a name and a **new** invitation
code (generate one: `prism invitation create <share-id> --recipient-hint Bob`;
the share id is in `prism shares list`). Approve in the server terminal, then
ask Claude: *"Use the prism-share tools: what does the shared conversation
say about authentication?"*

### C. ChatGPT, or a different machine (needs a public HTTPS URL)

Stop the local server, then serve behind a tunnel:

```bash
prism mcp serve --owner-label "Your Name" --approve-here --tunnel ngrok
# prints:  MCP endpoint for the recipient: https://<random>.ngrok-free.app/mcp
prism mcp check https://<random>.ngrok-free.app/mcp     # must pass before ChatGPT tries
```

- **Different machine:** install Prism there and run the same
  `prism recipient connect https://<random>.ngrok-free.app/mcp --headless --name …`.
- **ChatGPT:** enable developer mode and add a custom connector / plugin with
  that URL (menu names change; see OpenAI's
  [connect and test a plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)).
  ChatGPT starts the OAuth flow and opens the Prism consent page in your
  browser. ngrok's free domains may show a "you are about to visit" page first;
  click through it. Enter a name and a fresh invitation code, approve in the
  server terminal, then ask ChatGPT about the shared conversation.

Warning: the tunnel provider can see recipient traffic, and the URL is public
while the tunnel is up. Stop it when you are done.

## 5. Verification checklist

Do these against whichever recipient you used. Each should behave as stated.

| Check | How | Expected |
|---|---|---|
| Excluded turn never appears | `prism recipient query $U "BLUEHERON-4471" $D` | no matching passages |
| Excluded file never appears | `… query … "PRIVATE_FILE_CANARY"` | no matching passages |
| Included file appears | `… query … "OAuth"` | passage from `notes.md` |
| Everything is marked untrusted | any recipient output | a "Shared by … (Owner-authored, unverified content …)" line |
| No access before approval | connect and do not approve | recipient stays waiting; nothing readable |
| Invitation is one-time | run `connect` again with the same code | "invalid, expired, or already used" |
| Anonymous access refused | `curl -i -X POST $U -H 'content-type: application/json' -d '{}'` | `401` with `WWW-Authenticate` |
| You can see who read what | `prism audit list` | `recipient_access/manifest`, `/query`, … lines, no content |
| Instant revocation | `prism grants list`, then `prism grant revoke <grant-id>`, then the recipient runs `manifest` | `ACCESS_DENIED` |
| Denied recipient stays out | connect a new one, answer `n` when asked | recipient is refused |

## 6. Clean up

```bash
# stop the servers (Ctrl-C) and any ngrok tunnel
prism grants list                        # revoke anything still active
prism grant revoke <grant-id>
prism share revoke <share-id>            # or: prism snapshot revoke <snapshot-id> --purge
rm -rf "$HOME/prism-test-data" ~/prism-recipient-a ~/prism-test-files
```

Confirm the ChatGPT shared link from step 1 is deleted.

## 7. If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| `The shared page returned HTTP 4xx` | Link deleted, private, or workspace-restricted. Recreate a public link. |
| `did not contain one recognized data stream` / `unsupported …` | ChatGPT changed its page format, or the conversation has content Prism refuses (images, files, tools). Try a plain-text conversation. The capture fails closed on purpose. |
| `Refusing to publish … non-interactive` | Run `quickshare` in a real terminal, or use the individual commands with `--yes`. |
| `prism mcp check` reports the resource URL differs | The server must be started with the exact public URL: use `--tunnel` or `--public-url`. |
| Recipient hangs at "Waiting for the owner" | You did not answer the approval prompt (start the server with `--approve-here`) or run `prism grants list` and `prism grant approve <id>`. |
| Consent page says "invalid or has expired" | The 15-minute authorization window passed or five wrong codes were entered. Start `connect` again. |
| ChatGPT/Claude cannot complete sign-in | Run `prism mcp check <url>`; over a tunnel make sure you used the `https://` URL and that the tunnel is still running. |
| `429` errors | Rate limit reached; wait a few seconds. |
| ngrok exits immediately | Not authenticated: run `ngrok config add-authtoken <token>`. |

## 8. What this test does not cover

- ChatGPT and Claude connector sign-in has not been verified by the maintainers;
  this walkthrough is how you find out. Report what breaks and where.
- The Sealed Projection gate catches pasted secrets, obvious PII, and
  content that echoes something you excluded (deterministic pattern
  matching, not a model) — it is a backstop, not a substitute for reading
  the printed preview yourself before you confirm publication.
- The source conversation title is included unless you explicitly override
  it (`prism draft title --set "..."` / `--clear`).
- Your Prism database is not encrypted on disk.
- Recipient identity is "the person you approved", not a verified account.
