# M2.1b: Reviewed Handoff Preparation

Date: **2026-09-17 (America/Los_Angeles)**.

**Status:** Implemented on `prototype/m2-reviewed-handoff`, with focused automated checks and a browser acceptance walkthrough. Owner review is pending. The user explicitly authorized this increment after reviewing its manual-selection boundary. M2.1c, automatic boundary recommendations, remote deployment and new spending are not authorized by that instruction.


**Subsequent M2.1c checkpoint:** The user explicitly authorized [contextual collaborator continuation](m2-context-continuation.md), which is now implemented and awaiting review. Approved contextual versions are active and can be revoked; older statements below about blocked activation describe the prior delivery checkpoint. No broader M2 or private-pilot acceptance is implied.

## User outcome and scope

An owner chooses completed work from a project conversation, writes a handoff summary and open questions, selects evidence and historical results, reviews the exact disclosure, and approves an immutable version. The result is a persisted application record, not a portable image, live agent, process-memory snapshot or new collaborator session.

The preparation flow makes **no model calls** and runs **no experiments**. Selection is manual, with nothing checked by default. This supplies a concrete baseline for future preparation assistance without claiming automatic boundary inference or semantic privacy.

The UI distinguishes these states:

- **Needs review:** a frozen candidate exists but has not been approved.
- **Handoff saved:** its exact bytes have been approved; contextual collaborator entry is not connected yet.
- **Active:** an existing M1 file-only share remains usable by the legacy local Reviewer.

A saved contextual version is deliberately absent from the legacy Reviewer list, and direct session creation or reads cannot activate it. Silently dropping the selected background to start a file-only session would misrepresent the handoff. M2.1c will separately add background display, scoped context reads and model initialization.

## Implemented choices and boundaries

| Material | Owner operation | Enforced boundary |
| --- | --- | --- |
| Completed checkpoint | Choose a successfully completed turn | Only completed messages and runs from the same conversation, at or before that checkpoint, are eligible. Failed/running answers cannot become successful historical messages. |
| Excerpts | Select question and/or answer text; optionally edit it | Original text is resolved server-side. Answers include visible limitations; expanded claim details are not copied. Unchanged text is labelled verbatim; changed text is owner-edited. Excerpts retain chronological order. |
| Summary and next steps | Write and edit text explicitly | These are owner-written claims, not automatically generated facts or grants. No private history is automatically summarized. |
| Files | Select from the conversation's pinned revision | The candidate copies exact captured bytes, not the current filesystem. One to five files are supported by the fixed synthetic catalog. |
| References | Include supporting files/results or explicitly omit attachments | Missing selected support blocks materialization unless the owner selects an evidence-gap label. Omitting references does not redact message text. Included targets receive share-local IDs; private mappings remain owner-side. |
| Historical results | Include an actually completed record | The service resolves the result; browser-authored result payloads are rejected. A historical result preserves output and executable/input identities and does not replay a job. Observations, baseline and limitations must be explicitly selected with a result. |
| Permitted action | Choose Inspect or the existing fixed Verify action | Verify requires the same fixed synthetic inputs. No arbitrary command, new tool, network route or host access is added. |
| Approval and revision | Inspect the exact preview, acknowledge disclosure and approve its digest | The digest covers context, resources and executable definition. Changing selections produces a new unapproved version. Existing approved bytes are unchanged. |

The context schema stores summary, open questions, ordered excerpts with provenance, share-local citations and historical run records. Source conversation, checkpoint, message and resource mappings are stored separately in an owner-only table. Reopening an edit resolves that private selection only after owner/project authorization. It never returns this mapping through collaborator APIs.

Raw private source identifiers embedded in selected prose are rejected with an editing prompt. Structured citations are rewritten automatically; prose is not silently rewritten or sanitized. Owners can remove identifiers and refer to the attached historical result, with the edit explicitly labelled. This check is narrow: encoded identifiers, copied private facts or secrets inside owner-approved text are not proven absent. Excluding a file does not remove its information from selected messages, summaries or other files.

Source material and statements about approvals remain evidence. The handoff does not transfer provider threads, credentials, hidden reasoning, system roles, live processes or owner privileges. Referencing an old successful run does not authorize a new one.

## Limits and persistence

Reuse the [existing M1 setup](m1-local-sharing.md#run-the-current-increment). Build the frontend, refresh the local non-editable package and restart the loopback service only as necessary, preserving its state directory, explicitly configured provider and authorized aggregate model allowance. No new dependencies or cloud service are required.

- Additive `owner_handoff_sources` and migration-version tables preserve existing M1 and M2.1a data. Contextual candidates use manifest schema 2; existing schema-1 shares retain their behavior.
- At least one excerpt and one file are required. Up to 24 excerpt parts and six eligible historical results may be selected, subject to the existing source/session limits.
- Summary: 2,000 characters; next steps: 1,500; purpose: 1,000; edited excerpt: 5,000. The existing 16 KiB request-body ceiling and a 16 KiB materialized-context ceiling also apply. Oversized selections are rejected, never silently truncated.
- The existing 24-version local-store limit includes abandoned candidates and revisions. Version deletion/retention management is not implemented; do not reset the database to bypass this limit or the model ledger.
- Frozen candidates and approvals persist across refresh/restart. Unsaved preparation text is browser component state; a page reload can discard it. Use **Review exact handoff** to persist a candidate, then **Change selection** to reopen it.
- Optional numeric measurement remains off by default. When enabled, preparation records a selected-message count; existing approval timing/security events remain bounded. No raw prompt or hidden-reasoning telemetry is added.

## Acceptance walkthrough

Use the existing synthetic Owner conversation and its actual seed-7 result. Reviewing it costs no model allowance and requires no new runtime job.

1. In **Project chat**, click **Prepare handoff**. Confirm that no message, file or run is preselected. Choose the later completed checkpoint that includes the seed-7 result; failed turns remain excluded.
2. Fill **Handoff purpose**, **Owner-written summary**, and **Open questions and next steps**. For example, request a review of limitations and a future seed-23 comparison.
3. Select an answer that references the private note while leaving that file unselected. **Review exact handoff** remains unavailable until its support is included or the evidence gap is explicitly acknowledged. For the public demonstration, unselect the note-related answer and choose the later run-related question and answer instead.
4. Edit the run answer to remove its original private run ID and refer to the attached historical result. Preserve the synthetic-data limitation. Select `overview.md`, `observations.csv`, `baseline.json` and `limitations.md`; leave `private-notes.txt` unselected. Select the completed seed-7 result and **Verify**.
5. Choose **Review exact handoff**. Inspect the entire summary, excerpts, edit labels, historical output/provenance, file contents and fixed executable. The original Owner history is not changed by editing an excerpt.
6. Acknowledge the exact disclosure and choose **Approve this exact version**. Confirm **Handoff saved**, with an explicit notice that collaborator continuation waits for M2.1c. There is no contextual Reviewer launch button.
7. Reload, open **Shared projects**, then reopen the saved version. Choose **Prepare a revised version**, change the summary and freeze another candidate. It must require fresh approval while the original remains unchanged.
8. Open the legacy local Reviewer. Existing M1 shares remain available, while both the saved contextual version and its unapproved revision remain absent. Automated tests also probe the direct APIs; hiding a UI button alone is not the boundary.

The prepared browser demonstration contains one approved contextual version and one revised candidate awaiting review. Both contain two excerpts, four files and the copied real seed-7 record. The original private-note exchange is excluded. These are synthetic acceptance artifacts, not production handoffs.

## Verification record

See [the machine-readable record](validation/m2-reviewed-handoff.json).

- **50 focused automated tests passed:** 10 handoff, 11 owner, 13 sharing/API, 12 agent and 4 job tests. Handoff fixtures contain explicitly synthetic stored messages/results; model/transport/worker doubles in the existing suites are not live execution evidence.
- Direct checks cover cross-role/project/session access, forged result/role fields, CSRF, failed and future checkpoints, missing supporting evidence, historical-run eligibility, context/body bounds, atomic failure, immutable approval, private edit-mapping access and blocked legacy activation. Existing M1/M2.1a regressions passed.
- Ruff and frontend build passed. The real browser walkthrough exercised selection, expected rejections, explicit editing, exact preview, approval, revised candidates, reload, file/executable inspection and unchanged Reviewer availability. A 390px layout had no horizontal overflow.
- During UI iteration, a scroll-position effect returned a non-cleanup value and caused a React navigation error. It was corrected, rebuilt and rechecked through reload/navigation and saved-version inspection. Historical model failures from M2.1a remain recorded; this increment does not claim to fix them.
- The browser copied an existing actual seed-7 result rather than a mock result. No new model dispatches or runtime jobs occurred. The prior reservations, conversations, runs, versions and other pre-existing rows were preserved; only two candidates and their preparation/approval records were added. Current source files still match the existing Owner resource revision.

Limited passing checks do not prove semantic privacy, general reliability or absolute security. Arbitrary projects, multi-owner production identity, automatic selection, collaborator context execution and integrated Linux/Kata pilot isolation remain outside this increment.

## Next gate

Stop for owner acceptance or requested changes to M2.1b. The next proposed capability is **M2.1c: show the approved background to a fresh collaborator and let its scoped agent continue from it**, with separate normal and denial-path acceptance. Do not implement it while awaiting review.
