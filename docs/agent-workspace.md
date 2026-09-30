# U1: Approved Working Copies and Diff Review

**Historical U1 checkpoint:** This guide preserves the manual-only U1 scope. The later [U2 implementation](agent-workspace-tools.md) now adds scoped agent tools, separately approved exact-revision checks and downloadable returns; Chrome manual acceptance passed; live-model task completion remains pending.

**Date:** 2026-09-29. **Status:** Implemented in local development on `prototype/agent-workspace`. Browser acceptance is pending at this checkpoint; see [validation](validation/agent-workspace.json). This is the first authorized increment of [bounded task completion](agent-task-completion.md), not the completed agent task loop or a private-pilot release.

## What works

An owner creates a direct file handoff or a reviewed conversation handoff, chooses **Read + edit selected copies**, and explicitly selects which shared files may be edited. Approval binds the exact file bytes and editable IDs into a schema-3 immutable manifest. Existing schema-1/2 Inspect and Verify versions retain their behavior; old deployed code cannot activate schema-3 versions.

The collaborator opens **Working copies**, reads any shared copy, manually revises the approved editable subset, saves against the current revision, and inspects a server-generated diff against the approved original. Other included files are read only. An Inspect invitation to the same version and the local Observer role have no workspace editing access. Continue invitations are available only for separately approved schema-3 versions with explicit editable IDs. They authorize no execution in U1.

Workspace storage is a session-keyed text overlay in SQLite, not a writable mount of the owner source or a new running Kata guest. Unmodified bytes come from the approved immutable manifest; saved text never becomes a path or command. No filesystem source path is accepted by the edit interface. Working-copy changes survive an application restart, remain separate between sessions, and do not alter source files or approved evidence used by chat.

## Try it

Use the dedicated local demo on port 8766 if it is still running. Open its local owner/reviewer links from the launching terminal; local demo credentials are ephemeral and are not Google identities. This test service has no model credential and a zero model-call allowance. The existing private Linux service and its grants were not changed.

To start a dedicated instance from the repository root:

```bash
cd /Users/nozomi64/Documents/prism
npm --prefix frontend run build
uv run --no-editable prism demo \
  --data-dir /tmp/prism-u1-workspace-20260929 \
  --port 8766 \
  --model-budget-cents 0 \
  --project /Users/nozomi64/Documents/prism/examples/document-handoff/.prism-project.json
```

1. Owner → Shared projects → New share. Select **Document release handoff**.
2. Include `README.md`, `docs/release.md`, and `config/release.json`; leave `private/notes.txt` excluded.
3. Choose **Read + edit selected copies**. Select only `docs/release.md` as editable.
4. Review selected content. Confirm that only that file is marked **Editable copy**, then approve the exact version.
5. Open the local Reviewer link and start a session on that version. Open **Working copies**.
6. Open `docs/release.md`, add a reviewer note, and **Save copy**. Expect the revision to increase and the saved diff to show the added line.
7. Open `config/release.json`. Its text area is read only. Check **Sources**: original approved evidence still contains its original text. Owner source bytes remain unchanged.
8. Optional: open a second reviewer session on the same version. Its workspace starts at revision 0. A direct write to a read-only file or an excluded ID is denied by the service, independently of the disabled UI.

For reviewed conversation handoffs, choose the same capability and editable subset in the handoff builder. Source IDs are remapped to share-local editable IDs along with file selection. The exact-context approval acknowledgement continues to apply.

## Boundaries and limits

- Per operation, reauthorize current session, recipient, approved version, grant revision, expiry and revocation. Named browser authentication also binds the requested session. A version ID alone is not authority.
- At most eight selected files; UTF-8 text only; 32 KiB per file, 96 KiB for the complete current workspace, 2,000 lines for editable files, and 20 actual saves per session. Identical saves do not consume a revision; restoring original bytes removes that file's diff but still consumes a save.
- Every save requires the current global workspace revision. Competing saves serialize in the database; a stale save returns a conflict instead of overwriting newer work. Reload and inspect before saving again.
- Only the exact edit route has a bounded 256 KiB JSON envelope to accommodate escaped text. Existing interfaces retain their 16 KiB request cap; the decoded workspace text still has the smaller file/total limits.
- Diffs carry before/after content hashes and explicit final-newline information. Line-ending-only changes are reported even when normalized content lines are identical. Generated text is rendered as inert React text, not HTML.
- Revocation blocks subsequent workspace reads, edits and diff retrieval. It cannot erase copies already delivered. Workspace audit events contain actor/resource/outcome metadata, not edited content or model reasoning.
- Unsaved text is local UI state and is lost if the workspace view is closed. Save or discard before switching files.

## What is not connected yet

The model still reads approved original evidence, not edited working copies. It has no edit tool, workspace citation type, artifact export or validator for edited bytes in U1. The interface explicitly labels saved edits as unvalidated. No model answer, syntax success, runtime success or download is simulated.

U2 will connect scoped model tools, exact-revision validation and result/artifact delivery. U3 will validate useful follow-ups and completed-check reuse. Those are separately authorized increments. The private-release packaging has not been extended to install the new workspace module; no server release should be attempted using an older package manifest. M2 fault/recovery failures remain recorded, `pilot_ready=false`, and no cloud deployment, model call, push or merge was performed for U1.
