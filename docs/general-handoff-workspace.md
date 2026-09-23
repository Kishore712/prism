# General-Purpose Handoff Workspace

Date: **2026-09-19 (America/Los_Angeles)**. Branch: `prototype/general-handoff-workspace`.

## Authorized outcome

Prism is a general agent handoff product. The synthetic experiment is a test fixture, not the main product workflow. The user authorized replacing experiment-specific screens with conversation-first task requests and shared background, resources, permissions, and activity/results. A `gpt-5.6-sol` coding subagent implements the frontend; the parent reviews boundaries, validates the interface and maintains documentation.

## Scope and acceptance

- Owner and collaborator start work through conversation. No dedicated seed input or experiment-launch form remains.
- Activity and results present real recorded executions with status and provenance. Task-specific parameters and output are available inside details; imported historical results remain distinct from new work.
- Shared background and resource review remain usable. Exact approval still shows the actual permitted executable, parameters and limits.
- Permissions explain supported capabilities and excluded access. A general interface does not claim arbitrary tools, file editing or project import have been implemented.
- Missing-model, empty-activity and failed-answer states explain what the user can do without fabricating results. Existing records and model reservations are preserved.

Backend routes, grant enforcement, model configuration and the fixed synthetic action are unchanged. Removing the manual launch interface does not remove the underlying checked job API. All tool requests still pass server authorization and parameter/resource limits.

## Try the interface

1. Open Owner and select the existing project conversation. Use conversation to describe work rather than an experiment form.
2. Open **Activity & results** to inspect existing completed work. Expand a task's details for parameters, exact output and provenance. The existing experiment appears only as one recorded task.
3. Open an approved contextual share and enter a fresh collaborator session. Inspect the shared background and resources, then continue in conversation.
4. Review **Capabilities & permissions** to see what is actually allowed. The current fixture still has just one bounded executable action; broader tools are planned.
5. Inspect the collaborator activity view. A fresh session should have no invented activity; imported owner results remain in the approved background.

Viewing existing records needs no model call. This refinement makes no new paid calls and does not run another experiment. Do not reset or increase the existing model allowance to test presentation.

## Validation record

Implemented and ready for owner review. The production frontend build passed. Seventeen existing focused tests passed (13 sharing/API boundary checks and 4 job authorization/idempotency checks). Backend source files are unchanged from the pre-increment backup.

Browser checks used actual persisted data: Owner activity showed four prior conversation records (including failures) and one actual completed execution. Its exact task output remained available inside details. The approved handoff preview retained the executable limits and its historical result, with no fabricated historical timestamp. A fresh collaborator session displayed the approved background, empty new activity, generic chat suggestions, and accurate capabilities/permissions. Neither role exposed the manual seed/run form. The reviewer page had no recorded browser console errors.

All pre-existing database rows were preserved. Browser validation added one fresh session and its normal activity event; it made zero model dispatches, zero new conversation turns and zero new runtime jobs. The aggregate allowance remains $2.00 with $1.90 reserved. No cloud operation or publication occurred. This UI validation does not re-establish live model behavior, sandbox readiness or complete tool-call telemetry.

See [the validation record](validation/general-handoff-workspace.json).

## Next gate

Pause for owner review of this interface refinement. The next proposed capability remains the separately scoped M2.2 project import beyond the fixed fixture; it is not authorized by this interface request.
