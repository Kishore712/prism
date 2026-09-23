# Private live demo: owner to USC recipient

This runbook demonstrates a complete synthetic Document release handoff through the already-running private Prism service. The service stays running throughout, the USC test identity's grant remains active until its stated expiry, and the VM has no automatic stop configured. Do not revoke the grant or stop the VM as part of this demonstration.

## Demo layout

The owner and recipient browser flows are served by the same Linux Prism web service. The service authenticates each identity and applies separate owner and recipient scopes; the recipient receives only the reviewed handoff. The recipient's approved action is dispatched to a separate worker process and runs in a temporary Kata VM, which is removed after completion. Model inference is performed by the configured external provider. The browser interface, Linux service, worker, Kata guest, and inference provider are therefore distinct parts of one demonstration flow.

## Before opening the demo

Have these ready:

- Tailscale connected on the owner's Mac, with access to the private service. This walkthrough uses the same Mac for both browser identities; a separate recipient computer has not been validated and would first need tailnet access.
- Chrome with the owner's normal profile, a recipient session, and a window for Google Cloud Shell. Incognito windows share cookies while any Incognito window remains open: use the existing USC `/review` session for a quick demo, or close every Incognito window first or use a distinct clean Chrome profile for a truly fresh invitation session. Do not sign the recipient into the owner's Chrome profile.
- The owner Google account already authorized for `/owner`, plus the designated USC test Google identity. Keep account emails and exact OIDC issuer/subject values private.
- The authorized Google Cloud project, zone, and VM name, and Cloud Shell access to that project. Use the existing IAP SSH path.
- The existing synthetic **Document release handoff** project fixture. Use synthetic content only.

## Ordered setup

1. Connect Tailscale on the owner's Mac and confirm the private service is reachable. Keep the actual Tailscale host name private; use `<private-service-origin>` in this document.
2. Open the owner's normal Chrome profile and navigate to `<private-service-origin>/owner`.
3. Open a separate Chrome Incognito window for the USC recipient and navigate to `<private-service-origin>/review`. Do not sign the recipient into the owner's Chrome profile. Incognito windows share cookies while any Incognito window remains open; close every Incognito window before starting a fresh recipient identity session, or use a distinct clean Chrome profile.
4. Open a third window for Google Cloud Shell, authenticated to the authorized project. Do not put the real project, zone, VM name, or account identity in repository documentation.

Never paste invitation, login, session, or API tokens into notes, chat, screenshots, or shell history. The same Linux web service handles both browser paths with distinct scopes; it schedules recipient actions to the separate worker and ephemeral Kata guest described above.

Button labels below follow the current owner and recipient interface; a minor wording variation may appear after a frontend update.

## Owner: chat, review, approve, and invite

1. Sign in to `/owner` as the owner. Choose **Project chat** → **Document release handoff** → **New chat**. Select the three file buttons labeled **Use README.md in this conversation**, **Use docs/release.md in this conversation**, and **Use config/release.json in this conversation**. Do not select `private/notes.txt` or any unlisted canary file. Check the model disclosure box, then choose **Start private conversation**.
2. Ask: “From the selected release files, identify the release value, audience, and approved flag in `config/release.json`, then explain what remains to check according to `docs/release.md`.” The synthetic fixture should show `release=synthetic-2026-09`, `audience=[reviewer]`, and `approved=false`. Review the answer and its source references. This is a real model request; the selected context and question are sent to the external provider.
3. Choose **Prepare handoff**. Select the completed checkpoint and purpose for the release review. Add an owner summary such as: “This synthetic release is not approved. The collaborator may inspect the three selected release files and run only the JSON syntax check.” Review open questions and select the owner answer excerpt to include. Select only `README.md`, `docs/release.md`, and `config/release.json`. Under allowed work choose **Read material + Check JSON syntax**, then choose **Review exact handoff**. Confirm the displayed version contains only the intended summary, open questions, excerpt, files, and capability. Check the approval checkbox and choose **Approve this exact version**. If an already approved version with exactly this content and capability is intentionally being reused, the owner may go directly to its invite flow.
4. Choose **Invite a named collaborator** → **Create identity link**. This 15-minute identity link verifies who controls the USC test identity and creates no Prism grant. Copy the one-time identity URL directly into the prearranged private channel for the recipient; do not record it. Ask the intended USC test identity to open it in the separate Chrome Incognito window and complete Google sign-in.
5. Return to the owner window and choose **Check account**. Confirm the verified issuer and `sub` shown by Prism belong to the intended USC test person, without copying those values into notes or chat. Choose **Read + Check JSON syntax** as the allowed work and set the invitation lifetime to **24 hours**. Choose **Create invitation**, then copy the one-time invitation URL into the same private channel. This is the grant-bearing link; treat it as a bearer secret. A wrong identity cannot redeem it.

## Recipient: sign in, inspect, ask, and run

1. In the recipient browser session, open the one-time invitation URL and complete Google sign-in as the USC test identity. The invitation is redeemed once and establishes a fresh scoped session. For the quick-reuse path, use the existing signed-in USC session at `/review` without creating or redeeming another invitation.
2. Confirm the Document release workspace lists only the three approved files and the approved **Read + Check JSON syntax** capability. Open the shared files. In particular, inspect `docs/release.md` and `config/release.json`.
3. Submit one focused request that asks for both the sourced answer and the approved run: “Run the approved JSON syntax check on `config/release.json`. Report whether the JSON is syntactically valid and whether its `approved` field is false; explain that field using `docs/release.md`. Do not treat a false value as invalid JSON.” This uses the live external model route, which sends the question and permitted context to the provider for real inference. The fixed action checks JSON syntax; it does not judge whether release decisions such as `approved: false` are semantically correct.
4. Wait for the answer and run to complete, then open **Activity & results** and inspect the conversation record and execution result. Expand **Task details, parameters, and exact output** (or its equivalent) and copy the full 32-character task/run ID for the read-only Cloud Shell check below.

## Show access boundaries

In the recipient workspace, open **Capabilities & permissions** → **Session and version identity** and note the session ID for use only in the browser address bar. Keep the `/review` tab open. In a second Incognito tab, visit `<private-service-origin>/api/review/sessions/<session-id>/evidence/unlisted-canary.txt`; it must be denied. Then visit `<private-service-origin>/api/owner/state`; it must also be denied. Return to the original `/review` tab and continue the run. The browser sends its session cookie automatically; do not copy cookies, authorization headers, tokens, or private API values into the runbook or terminal.

## Linux host: inspect the completed Kata run

On the configured owner's Mac, the saved, host-key-pinned IAP connection needs no project or VM placeholders. Open Terminal and run:

```bash
prism-ssh --check
prism-ssh
```

The first command checks the current VM/firewall boundary and SSH connection. The second opens an interactive Linux shell; `exit` closes only that SSH session and local IAP tunnel. It does not stop the VM. Google account authorization may need renewal. If `prism-ssh` is not on the shell's `PATH`, run `$HOME/.config/prism/ssh/prism-ssh` instead.

Alternatively, in a third browser window, open Google Cloud Shell for the authorized project and connect to the existing VM using IAP SSH. This repository does not publish the deployment's project and instance identifiers; obtain those values from the private deployment configuration:

```bash
gcloud compute ssh <vm-name> \
  --project=<authorized-project-id> \
  --zone=<vm-zone> \
  --tunnel-through-iap \
  --ssh-key-expire-after=1h
```

After connecting, first confirm the web service is still active. Then run the read-only checks below. The Python query opens the root-only database using SQLite read-only mode and lists the five most recent `json-check` records. Match the full 32-character `id` to the task ID shown in **Task details, parameters, and exact output**; do not assume the newest record is yours if other people ran tasks. `uname -r` reports the host kernel; the stored result reports the Kata handler, guest kernel, exit result, and confirmed cleanup. The final commands show the installed Kata runtime version and verify its task list is empty after cleanup.

```bash
sudo systemctl is-active prism-identity-service.service
uname -r
sudo python3 - <<'PY'
import json
import sqlite3

db = sqlite3.connect(
    "file:/var/lib/prism-identity/service-state/demo.sqlite?mode=ro",
    uri=True,
)
db.row_factory = sqlite3.Row
rows = db.execute(
    "SELECT id, status, action, runtime_profile, result FROM runs "
    "WHERE action = 'json-check' ORDER BY created DESC LIMIT 5"
).fetchall()
if not rows:
    raise SystemExit("No json-check runs found")
for row in rows:
    result = json.loads(row["result"] or "{}")
    print(json.dumps({
        "id": row["id"],
        "status": row["status"],
        "action": row["action"],
        "runtime_profile": row["runtime_profile"],
        "runtime_handler": result.get("runtime_handler"),
        "guest_kernel": result.get("guest_kernel"),
        "exit_code": result.get("exit_code"),
        "cleaned_up": result.get("cleaned_up"),
    }, indent=2))
db.close()
PY
sudo /opt/kata/bin/kata-runtime --version
sudo /usr/local/bin/ctr -a /run/containerd/containerd.sock -n prism-m0 tasks list
```

For a successful run, `systemctl is-active` should print `active`; `uname -r` prints the host's Linux kernel release. The record matching the UI task ID should show status `completed`, action `json-check`, profile `reference-linux`, runtime handler `io.containerd.kata.v2`, exit code `0`, and `cleaned_up: true`. `kata-runtime --version` should print the installed runtime version. The explicit containerd socket avoids an ambiguous default endpoint; the task list should show its column header and no task rows. Preserve the host kernel and exact run ID in private demo notes only if needed; do not add a fresh run ID or machine identity to repository docs. The approved task is ephemeral: its isolated Kata task is removed after the run, and the stored result is the service's record. The read-only database query inspects those records; it does not rerun a task.

## Invitation recovery and limits

If the recipient loses the invitation before redeeming it, the owner can issue a replacement for the same approved version and verified identity. If the one-time URL was already redeemed but the recipient loses the session cookie, the consumed invitation cannot restore that session; issue a new invitation. Do not change existing grants during this demo. Each new invitation has its own expiry, shown in the owner interface; a new session does not extend an earlier grant.

The private Linux identity service's aggregate model reservation ceiling is **$10.00 (1,000 cents)**. The previous **$0.80** reservation remained recorded at the time of the change, leaving **$9.20 at that checkpoint before subsequent demo calls**. Check the live interface for the current remainder. Refresh the allowance display for the current remainder. This is an application reservation ceiling, not a hard provider billing cap: provider prices, rounding, and billing are controlled by the provider. The ceiling remains in effect until the user explicitly ends the demo. Restoring the former ceiling requires an explicit user request. A finite app-side ceiling does not itself guarantee or enforce a provider account spending limit.

This is a bounded synthetic demonstration, not a broad security proof or production-readiness assessment. Keep `pilot_ready=false`. Leave the VM running, leave the existing grant active, and do not revoke the USC grant as a demo cleanup step.
