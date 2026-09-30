# Agent Utility Increment: Complete a Bounded Project Task

**Latest direction (2026-09-30):** Local application model accounting is now [explicitly uncapped](model-accounting.md), preserving the original ledger. U3's document-follow-up repair passed; unnecessary clarification permission requests remain a behavior limitation. The next [U4 computation proposal](agent-computation-scenario.md) replaces the small parser demonstration with an actual read/code/compute/report/return task and recalculation. Its Python execution boundary is proposed, not implemented.

**Date:** 2026-09-29. **Status:** The owner approved proceeding after scope review. U1 is implemented locally with automated checks; the later U2 Chrome walkthrough passed manual copy/edit/diff acceptance. Owner review is pending. U2 scoped tools, exact checks and returns are now locally implemented with automated and real development-worker evidence; Chrome manual acceptance passed; one repaired real-model task passed. U3 and the complete agent task outcome are not accepted. See [U2 delivery](agent-workspace-tools.md). **Dependency baseline:** [preserved checkpoint](agent-workstream-checkpoint.md). See [U1 usage and limits](agent-workspace.md).

## Observable outcome

**Current U3 checkpoint:** [Follow-ups and exact-input reuse](agent-workspace-followups.md) are implemented locally. Five real-model cases yielded four completed answers and one failed final answer, with an additional clarification quality limitation. Repairs and regression checks are recorded, but repair live acceptance and owner review remain pending. Continue within U3 before any later stage; the original $5 ledger has 460 cents reserved and 40 remaining.

A collaborator gives the handed-off agent one concrete project task. The agent reads the approved materials, identifies relevant inconsistencies, makes permitted changes in a session-owned workspace, runs a permitted validator, and returns an inspectable deliverable with evidence. A follow-up can revise that deliverable without discarding prior work or automatically repeating a completed check.

Success means an actual useful change and its checkable files, not a fluent answer, a static plan, a mock tool response, or a button labelled Execute. This is a narrow M3 Continue candidate; the unfinished M2 pilot gate stays open. Local development acceptance precedes any separately scoped private-server release.

## First demonstration

Prepare a small synthetic documentation/configuration package using the existing explicit project manifest. Its approved requirements specify a release identifier, required audience, and document list; its configuration and release notes deliberately disagree in two places. The owner shares only the required files and authorizes edits to a specific subset. Include an excluded private canary outside that subset.

The collaborator asks: "Prepare this package for reviewer handoff: reconcile the configuration and release notes with the supplied requirements, keep review approval pending, validate the edited JSON, and return the changes with any remaining blockers."

The agent must inspect the requirements and relevant files, explain a short work plan, apply the two justified corrections in its own workspace, run the fixed validator on those edited bytes, and return a patch/files plus a concise review memo. Approval markers are not set to approved merely to make the task look successful. A follow-up asks for a changed label or reviewer wording; the new deliverable and validation must refer to the revised workspace version.

This scenario exercises general project handoff rather than an experiment-specific interface. It establishes usefulness only for this workload; coding, web research, arbitrary scripts, and broad project compatibility remain future tasks.

## Acceptance criteria defined before implementation

| Case | Required observable result |
| --- | --- |
| Finish the task | The expected two inconsistencies are corrected; requirements not implicated by the request are preserved. The reviewer receives actual changed files and a diff, with no manual editing needed to complete the requested changes. |
| Verify the delivered bytes | A real fixed check runs against the exact edited workspace revision. Its result includes input hashes and run provenance; it cannot be presented as a check of a later revision. Syntax validity is not represented as proof of requirement correctness. |
| Explain the outcome | The answer identifies changes, references the source requirements and actual check, and lists unresolved blockers. A short user-visible plan/status is sufficient; hidden reasoning is neither required nor recorded. |
| Continue usefully | A follow-up reads existing task/workspace state, changes the intended file, and reports a new diff/revision. Asking what a completed check found retrieves its record without launching another check. |
| Handle missing or conflicting requirements | The agent states the concrete missing fact or conflict and asks a targeted question; it does not invent a value, approve a release, or guess hidden file inventory. |
| Enforce the boundary | Direct tool/API attempts to read an excluded canary, write a read-only file, use a path/link escape, access another session, or modify the owner source fail. Imported document instructions cannot grant authority. |
| Stop and preserve work | Budget/time exhaustion, failed validation, and revoked access produce accurate partial/failed status. Uncertain actions are not silently retried; already completed artifacts are not delivered after access is revoked. |

Freeze expected changes and scoring before the first real-model run. Report deterministic enforcement tests separately from live model/runtime evidence. Run at least the task, follow-up, completed-check reuse, and missing-requirement cases with the configured real model within the existing finite allowance; retain failures and token/request/runtime measurements. Unrun cases stay unverified. Small-sample success is demonstration evidence, not a reliability rate or research result.

## Recommended implementation

Retain the installed PydanticAI single-agent foundation and current external provider initially. Reuse its typed tools, dependencies, and model loop; first check capabilities against the locked version rather than upgrade by default. There is no demonstrated need for multiple agents, framework migration, fine-tuning, or a new model route in this increment.

1. **Trusted session workspace:** materialize only approved copied files; bind the workspace to recipient, session, version, and grant. Store explicit editable file IDs, finite text size/revision quotas, and private artifact metadata outside model control. Keep the owner's source immutable.
2. **Scoped tools:** reuse scoped evidence reads, add typed workspace reads/edits, diff inspection, fixed validation of a workspace revision, and artifact retrieval. Models address service-assigned IDs and revisions, not arbitrary host paths or commands. Version checks prevent overwriting intervening changes.
3. **Visible task state:** retain the user objective, short plan, actual workspace revision, evidence references, completed checks and blockers in that session. Model suggestions are candidate plans, not permission. Server records establish tool effects; no client-supplied history or hidden reasoning becomes trusted state.
4. **Real validation and delivery:** extend the existing Jobs/Kata path deliberately so the fixed JSON check validates an immutable copy of the edited revision with input hashes. Return a service-generated diff and bounded file artifacts. Artifacts are inert downloadable data; owner application of a patch remains explicit and separate.
5. **Bounded useful loop:** observe task status and tool results to decide the next permitted step, reuse existing completed work when inputs match, and stop at the configured budgets. Evaluate the current four-request/eight-tool limits against this workload first; any required increase needs an explicit finite proposal and preserved aggregate ledger.

Do not route all tools through a generic shell. Each operation is reauthorized server-side; file quotas, grant checks, path handling, runtime isolation and result delivery stay outside model authority. A prompt or framework approval object cannot authorize writes. See the official [PydanticAI toolsets](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/), [history trust guidance](https://pydantic.dev/docs/ai/core-concepts/message-history/), and [OWASP agent tool least-privilege guidance](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html), checked on 2026-09-29. These references inform implementation; they do not verify Prism's planned boundary.

## Delivery order and decision

| Increment | Independently usable outcome | Dependency |
| --- | --- | --- |
| U1: Review a proposed revision — locally implemented | Owner explicitly grants a small editable subset; collaborator manually produces and inspects a session-only revision/diff, while read-only/private/source writes are denied. | Owner authorized starting this increment; local checks and later Chrome manual acceptance passed; owner review pending. |
| U2: Finish and return the task — locally verified, owner review pending | Typed tools edit copies, validate exact bytes and freeze actual downloadable files/diff. | Tools/API and real development-worker checks passed; Chrome manual path passed; one repaired real-model task passed; owner acceptance pending. |
| U3: Maintain useful follow-ups | Agent revises a deliverable, reuses completed checks appropriately, and handles a missing requirement without guessing or duplicate work. | U2 and frozen regression cases. |

Report after each usable increment and wait for the agreed next authorization. The target completion demonstration is U2 plus the U3 follow-up cases. Remote deployment is a separate release step after local acceptance and applicable M2 controls. This proposal does not authorize public access, arbitrary code execution, unlimited model spending, automatic source writeback, or independent destination handoff.

## U2 live task checkpoint

**U2 live-model follow-up (2026-09-29):** Two bounded real-model attempts used the explicitly configured local Prism key and original $3 reservation ledger. Both corrected the two synthetic inconsistencies and completed actual development-container checks; the second also generated a hash-verified immutable return. Neither completed its final answer: first, the provider returned incomplete status; second, the request/tool allowance was reached. The finer failure causes were not retained. The existing ledger moved from $2.60 to $3.00 reserved and is exhausted; no reset or additional allowance occurred. A local tool refinement lets an explicitly requested check also freeze the same completed revision, denying publication after failure, intervening edits or revoke. Sixteen focused tests and 464 backend tests passed. See [live evidence](validation/agent-workspace-live.json). Full agent task acceptance, owner review and remote release remain pending; `pilot_ready=false`. Next: seek approval for a five-request turn and $3.50 total on this same ledger for at most two task reruns; this proposal is not yet authorized.


## Finite extension result

**U2 bounded extension result (2026-09-29):** The owner approved the finite extension to a $3.50 total on the original local ledger and at most two task attempts. Continue sessions now permit five requests; function tools remain hidden on requests four and five, and other modes retain four requests. Both real-model attempts corrected the synthetic files, completed actual development-container checks and froze returns; both failed the final structured answer. The second captured a reference to unavailable handoff background. Exact edit hashes now return in the same transaction, final correction gets the specific trusted denial, and failed turns preserve partial usage counters. A final allowed-context prompt clarification passed deterministic checks but has no further paid acceptance. All $3.50 is now reserved; no ledger reset occurred. Thirty-five focused workspace tests and a 467-test backend checkpoint passed. See [extension evidence](validation/agent-workspace-live-extension.json). Complete task acceptance remains failed; owner review, U3 and remote release are pending, with `pilot_ready=false`. Next: review the recorded output-reference failure before authorizing any further paid acceptance.


## Final-output reference repair

**U2 output-reference repair checkpoint (2026-09-29):** Continued the same feature without new paid calls. File-only collaborator versions now use a strict final-output schema requiring empty background and historical references; versions with approved background and the owner schema retain their existing output structure. The prompt now uses trusted request/tool limits, including the fifth Continue final-only correction. The Responses SDK/fake-HTTP test verified the outgoing strict constraint and independent rejection/correction of deliberately invalid background, with function tools hidden during repair. Twenty U2 tests, the retained sixteen U1 checks and all 468 backend tests passed, including context-bearing handoff regression; targeted Ruff/format passed. See [repair evidence](validation/agent-workspace-output-repair.json). The original ledger is unchanged at 70 requests/$3.50 reserved and has no remaining allowance. Historical live failures remain; complete autonomous task acceptance is still pending. Next: obtain explicit approval for one real task rerun with a $3.75 total (at most 25 additional cents reserved). No such extension, remote release or U3 has been executed.


## Current real-task outcome

**U2 real-task pass (2026-09-29):** After the owner approved a $5 local total and the requested single repaired-task rerun, the existing model completed read → two-file session-copy edit → actual fixed JSON check → immutable return → final answer in four requests/three tool calls, with 13,145 input and 578 output tokens. Independent ZIP scoring matched the required release/audience, pending approval, unchanged requirements/source, exact revision/hash/check and actual answer references. No final correction was needed. The original ledger now has $3.70 reserved/$1.30 remaining; all old failures remain. See [live pass](validation/agent-workspace-live-pass.json) and [repair checks](validation/agent-workspace-output-repair.json). All 468 backend tests passed. This is one synthetic local-development task via a trusted driver, not a reliability rate, new browser identity acceptance or Linux/Kata release. Owner review remains pending; `pilot_ready=false`. Next: owner acceptance of U2, then separately authorized U3 follow-up/reuse/missing-requirement cases. No U3, remote release, commit or push occurred.
