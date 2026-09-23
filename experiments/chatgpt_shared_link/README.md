# ChatGPT shared-link compatibility spike

This isolated experiment answers whether Prism can deterministically inspect a
public ChatGPT shared link without a browser extension, ChatGPT credentials, a
model call, or execution of provider JavaScript.

It is not a supported provider adapter. The observed provider page format is
undocumented and may change. Production code must not depend on this spike
until the Phase 3 design and contracts are approved.

## Safety properties

- Accepts only canonical `https://chatgpt.com/share/...` URLs.
- Supplies no cookies or account credentials.
- Rejects redirects and caps the uncompressed HTML response at 8 MiB.
- Parses inline data as text and JSON; it does not execute JavaScript.
- Writes no response body, shared URL, title, transcript, or native identifier.
- Prints only counts, fingerprints, structural categories, and timings.

The public link is still a bearer capability. Do not put it in shell history,
logs, committed files, or test fixtures. Revoke it in ChatGPT after testing if
it should no longer remain accessible.

## Run

```bash
cd /Users/kishore/Projects/prism
.venv/bin/python experiments/chatgpt_shared_link/inspect_shared_link.py
```

Paste the link into the non-echoing prompt. Passing it as a positional argument
is supported for automation, but may expose it through shell history or process
inspection and is not recommended for owner use.

The current experiment recognizes the React Router streaming page observed on
2026-09-19. It extracts the flattened root record, locates one conversation,
uses its linear path, and retains only non-empty, non-redacted, non-hidden,
finished `user` and `assistant` text messages. Messages are grouped by the
provider's exchange identifier to assess compatibility with Prism's current
one-user/one-assistant turn contract.

## Interpretation

- `canonical_v1_compatible` means the inspected sample can map to the existing
  `CapturedSession` message/turn shape after the documented filters.
- It does not mean all ChatGPT shared links are compatible.
- `raw_roles` and `raw_content_types` show why copying every provider record
  would be incorrect.
- `content_reference_types` reports only type names; linked content is neither
  fetched nor printed.
- The raw page hash is intentionally omitted because provider flags and request
  metadata can change independently of the conversation.

The repository must use synthetic structural fixtures for automated tests.
Never commit an actual shared page or transcript.
