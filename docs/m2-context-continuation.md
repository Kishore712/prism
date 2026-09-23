# M2.1c: Continue from Approved Handoff Context

Date: **2026-09-17 (America/Los_Angeles)**.

**Status:** Implemented on `prototype/m2-context-continuation` after explicit user authorization. Ready for owner review, not owner-accepted. This increment activates the manually reviewed synthetic handoffs from M2.1b. It does not authorize the remaining M2 backlog, cloud operation or an allowance increase.

## Delivered behavior

A collaborator opens an approved contextual version and sees the owner-written summary, next steps, selected excerpts, edit/gap labels and historical results. “Continue in your conversation” opens a fresh conversation. The server supplies only the immutable approved background, permitted file catalog, current session history and its execution records to the agent. It does not attach the owner's conversation or restore its provider state.

The existing scoped tools remain the only tools: search/read evidence, submit the fixed bootstrap with an integer seed, read a current-session run, and propose an access request. Inspect-only sessions cannot execute, including through direct requests. No arbitrary tool, MCP configuration, shell, credentials, active process or model memory is imported. Reading background never automatically executes its suggested next steps.

Four reference categories have separate validation and display:

| Reference | Meaning and enforcement |
| --- | --- |
| File citation | Approved file ID and valid numbered range; never an owner-private source ID. |
| Background reference | `summary`, `open_questions`, or an exact approved excerpt ID; owner assertions, not independently verified findings. |
| Historical owner result | Exact result ID inside this version's approved context; never accepted as a current-session run. |
| Current-session run | A completed execution belonging to this session; no imported or other-session job ID. |

Shared versions retain their exact approval digest. Version format and integrity, current approval, role binding, expiry and revocation are checked on access. The background and historical-result endpoints reauthorize every request. Model dispatch retains the existing authorization and persistent allowance checks. Historical source mappings stay owner-only. Legacy file-only versions remain usable without invented background.

## Acceptance walkthrough

1. Open **Owner → Shared projects**. Open an approved contextual version (the prepared demonstration begins `f3f13e60c785`) and inspect its exact content. An unapproved revised candidate remains unavailable to reviewers.
2. Choose **Open reviewer**, then **Start session** for that version. The landing page is **Shared background**, showing two approved excerpts, four files and the historical seed-7 result in the prepared example. No private note or later owner history is included.
3. Choose **Continue in your conversation**. A new session initially has no collaborator messages or new runs. Existing background is separate from the transcript.
4. With an explicitly configured model and enough existing allowance, ask a narrow contextual question. For example: “Summarize the handoff summary and next steps without running anything. Reference the background.” Only approved background and this session's work are sent to the configured external provider.
5. Ask to actually run seed 23 and compare it with the historical seed 7. Inspect **Verification** for a completed new job. A failed answer does not imply a failed job: check the job before retrying. To recover, ask to read the already completed run without executing again.
6. Click a **Historical owner result** reference: it opens the imported result with a historical label. Click a **Run** reference: it opens this session's verification records. Open **Sources** to read the approved files and their identities.
7. **New session** gives a fresh transcript with the same fixed background. **Choose another share** returns to the approved-version chooser. Owner's private chat and source work remain separate.
8. On a disposable reviewed version, revoke access in Owner, then try another background read, file read or model turn. Subsequent access is denied; previously displayed/saved content is not recalled. Owner work is unaffected.

The delivered reviewer tab retains the actual two-turn acceptance conversation: first a failed structured answer after a successful real run, then a successful comparison using the existing run. Browsing this record, background, sources and results does not spend model allowance. The aggregate ledger has $1.90 reserved out of the explicitly approved $2.00; only $0.10 remains at this checkpoint. Do not reset the ledger or increase the allowance without authorization.

## Verification and evidence

See [the machine-readable record](validation/m2-context-continuation.json). Fifty-four focused tests passed: 14 handoff, 12 agent, 11 owner, 13 sharing and 4 job tests. They include approved-context assembly with synthetic `FunctionModel` responses, fresh-session history, excluded canaries/private IDs, reference forgery, inspect-only execution denial, endpoint authorization, revoke/expiry-related regressions and aggregate dispatch controls. Doubles do not establish live model behavior.

The separate live browser check made two real model turns and four provider dispatches, reserving $0.20 without resetting prior usage. The first turn executed seed 23 in the existing development container but failed final citation-schema validation. After more explicit reference field descriptions and a narrower no-rerun recovery question, the second turn read the completed job and successfully compared it to the imported seed 7, with separate validated reference buttons. No second execution occurred. This is a recovery demonstration, not evidence that broad requests always succeed.

Actual seed-23 output: mean difference 0.0225; bootstrap interval approximately [0.0025, 0.04125]; eight synthetic pairs and 300 resamples. The job exited 0, took 0.403 seconds and reported container removal. Historical seed-7 interval: approximately [0.005, 0.0425]. Neither is scientific evidence from a real dataset.

Desktop and 390px background layout, version chooser, source panel, historical-result panel and current execution view were inspected in the browser. The frontend built and changed Python files passed lint. Prior database rows were preserved; no cloud host was started.

Reproduce focused checks from the repository root:

```sh
uv run --no-editable python -m unittest discover -s tests -p test_handoff.py -v
uv run --no-editable python -m unittest discover -s tests -p test_agent.py -v
uv run --no-editable python -m unittest discover -s tests -p test_owner.py -v
uv run --no-editable python -m unittest discover -s tests -p test_sharing.py -v
uv run --no-editable python -m unittest discover -s tests -p test_jobs.py -v
uv run --no-editable ruff check src/prism/sharing.py src/prism/conversation.py src/prism/webapp.py tests/test_handoff.py
```

Build with `npm run build` inside `frontend`, then use the existing [local startup guide](m1-local-sharing.md). Retain the approved `--model-budget-cents 200` on this configured demonstration. No credential is included in this documentation.

## Limits and next gate

- Only the configured synthetic project and fixed bootstrap are supported. Automatic boundary inference and arbitrary project/tool import remain deferred.
- Local credentials identify roles, not named people. Two users sharing a reviewer credential are not isolated identities. Separate transcripts are not a substitute for recipient-bound authentication.
- The development container is not the integrated Linux/Kata pilot executor. No stronger termination/lease guarantee or absolute safety is claimed.
- Context is quoted data, not a privileged message or a transferred running agent. The approved bytes may still contain misleading text; manual review and reference validation cannot prove semantic privacy or factual correctness.
- A 16 KiB frozen context and the existing 32 KiB model request limit remain enforced. Long context plus conversation history can exhaust the request limit; start a fresh session or prepare a smaller reviewed version. No automatic truncation silently changes approval.
- Structured model output can fail. Completed jobs survive a failed answer and must be inspected before retrying execution. Historical failures remain visible.
- Revocation stops later authorization; already delivered content cannot be recalled. This increment does not establish all pilot lifecycle guarantees.

Stop for owner acceptance. Next proposed increment follows the roadmap: import one explicitly supported project format beyond the fixture, with exact resource review and a bounded action/environment contract. Scope that increment before implementation; do not start it while awaiting review.
