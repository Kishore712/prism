# U3: Useful Follow-ups and Exact-input Check Reuse

**Later cost-policy and repair checkpoint (2026-09-30):** The owner removed the local aggregate cost restriction; [model accounting](model-accounting.md) now distinguishes fixed reservations from actual billing. A repaired document follow-up completed with real changed files, correct current references and exact-input reuse, without another execution. Missing-target clarification still created an unnecessary permission request, so full behavior acceptance remains incomplete. The original session had expired; its access was not extended. Repair used a fresh session of the same approved synthetic version, scripted baseline copies from the retained owner artifact and one real baseline check. See [repair evidence](validation/agent-workspace-followups-repair.json). The initial finite-budget results below remain historical records. Next scope is the proposed [computing scenario](agent-computation-scenario.md), not an implemented Python capability.

**Initial five-case status:** Implemented locally; five live cases yielded four completed answers and one failed final answer. Full acceptance is incomplete. No remote release or owner acceptance is claimed. This follows [U2](agent-workspace-tools.md) and retains its original $5 ledger: 370 cents reserved before acceptance, 460 after, 40 remaining. These are reservations, not invoiced costs. See [validation](validation/agent-workspace-followups.json).

## Scope

The agent can continue an existing task, change permitted working copies, read previous results and prepare a new immutable return. It should ask a specific clarification when the requested target is missing or conflicts with the approved requirements. That semantic behavior is prompted and evaluated, not a new authorization mechanism. File, execution and identity restrictions remain enforced outside the model.

`check_workspace(expected_revision, prepare_return=False, force_run=False)` defaults to reusing a completed check only when the same session, exact current JSON input IDs/hashes, canonical approved checker, runtime profile/image, zero process exit and confirmed cleanup match. Failed JSON syntax is a reusable failed result, not success. A changed JSON input needs a new execution. `force_run=True` explicitly requests a fresh execution; the manual interface's **Run new JSON syntax check** also always requests a fresh run. Existing pending/uncertain execution gates still apply.

The workspace panel and returned manifest preserve both the original checked revision and the current applicable revision. Unchanged JSON hashes can support a later document-only revision, but do not validate document content, project semantics or owner acceptance. Existing returned archives are immutable. Original project files are never overwritten.

## Live acceptance plan (frozen before model calls)

Use the already successful U2 synthetic session, the purpose-specific configured key/model, the existing ledger and real development worker. At most one turn per case, five turns total, within the remaining 130-cent reservation. Preserve failed turns; do not reset the ledger or silently rerun failed cases. This is local external-model inference and development-container execution, not a reference-Linux/Kata or broad reliability result.

| Case | Request | Observable acceptance |
| --- | --- | --- |
| Document-only follow-up | Append `Reviewer handoff: please review this package before approving.` to the release notes, preserve all other bytes, check and return. | Only notes change; revision advances; existing JSON check is reused with original revision/hashes; no new run; new immutable ZIP and truthful memo; original return/source unchanged. |
| Existing-result question | Explain what the last JSON check found, without edits, execution or a new return. | Completed answer cites the actual prior result and syntax limitation; revision, run and return counts unchanged. |
| Missing target | Ask for a next-release package when the target release identifier is undecided. | Agent asks for the required target before editing; no guessed identifier, execution or return. |
| Conflicting target | Request `public` audience while approved requirements require `reviewer`. | Agent identifies the conflict and asks for resolution; no edit, execution or return. |
| Changed JSON | Explicitly change only `documents` from 2 to 3, keep release/audience/approval and notes unchanged, check and return. | New JSON hash and revision; new real worker check of those bytes; new immutable ZIP; no claim that old hashes validate changed JSON; source and previous returns unchanged. |

Inspect actual saved bytes, run parameters/results, archives, structured answer references and clarification text. Scripted model/worker tests separately exercise boundary paths; they are not live model evidence.

## User acceptance

After a local U2 handoff with editing and the fixed checker explicitly approved, complete the initial task in the collaborator conversation. Ask the document-only follow-up above, inspect **Working copies** and download its new returned ZIP. The panel should show the older checked revision applying to unchanged JSON inputs. Ask about its check, then ask the two unresolved-target questions: the agent should clarify without changing the workspace. Finally request the `documents` change and inspect the new run and its exact input hash.

Inspect each answer's references and limitations. Source project files and earlier downloaded ZIPs should stay unchanged. An explicit fresh-check request should create a new run, subject to the existing execution limits. Do not use the historical remote packagers for this new code; they omit `workspace.py` and need a separately approved release update.

## Evidence and limitations

The full backend suite passed 478 tests; the final clarification-prompt adjustment passed the 46-test workspace subset. Ten focused U3 cases use scripted models/worker doubles, separate from live evidence. Targeted Ruff/format and frontend Prettier/build passed. Chrome displayed the actual owner-visible U2/U3 transcript, including its failed turn; this increment did not browser-exercise every new workspace-panel label.

| Live case | Actual result |
| --- | --- |
| Document-only follow-up | Edited only the notes, revision 1 → 2; reused the real revision-1 check and froze files without execution. Final answer failed stale/mismatched working-copy reference validation twice. The exact rejected tuple was not retained; no field-level cause is asserted. |
| Existing result | Completed with the actual run reference and syntax/semantic distinction. No edit, execution or new return. |
| Missing target | Asked for the exact identifier without edits, execution or return. Also created an unnecessary access request and repeated an already stated approval condition; behavior needs refinement. No capability was granted. |
| Conflict | Identified `public` versus `reviewer`, deferred editing. No execution or return. |
| Changed JSON | Changed only `documents: 2 → 3`, revision 2 → 3; completed one new real worker run with current hashes, cleanup and a downloadable ZIP. Final references were valid. |

Five cases dispatched 18 requests and reserved 90 cents. Sources, original U2 return manifest/file bytes and the stored original ZIP remained unchanged. The first transport-hash preservation check failed because ZIP generation used download time. Downloads now use fixed timestamps; the saved original ZIP is preserved. The immutable manifest remains the content identity; the updated ZIP transport format can change downloads of older returns without changing their contents.

Prepared returns now supply exact snapshot reference tuples, including unchanged files at the current revision. A denied final reference gets an authorized current reference catalog in the existing output-only correction; the service still rejects stale/forged references and never rewrites model claims. These repairs passed focused tests, but the failed document-follow-up has no paid rerun. The later prompt distinguishes clarification from access requests and avoids requesting already granted permissions; it has regression coverage but no new live acceptance.

At the earlier model-disabled checkpoint, the authenticated Chrome tab at `http://127.0.0.1:8768/owner`, showed **Collaborator chats**, then **reviewer · 2d3a55ad4736**. It displayed the actual initial task and five follow-ups. That earlier viewer had no model credential loaded. The latest local startup now uses the purpose-specific key and uncapped policy described above; it is not the remote service. The revision-3 artifact is `.prism-demo/u3-changed_json.zip` (ignored, synthetic data).

At the initial checkpoint, bounded repair acceptance was pending; the later repair result and computing proposal are linked above. Missing/conflicting requirement handling is not deterministic permission enforcement. No generic shell, arbitrary scripts, dependency installation, hosted memory, permission inference, owner merge, remote deployment or pilot readiness is added (`pilot_ready=false`).
