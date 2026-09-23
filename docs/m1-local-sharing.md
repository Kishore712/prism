# M1: Local sharing vertical slice

**Subsequent checkpoint:** The user separately authorized [M2.1a owner project chat](m2-owner-workspace.md#current-delivery-record), now implemented and awaiting review on `prototype/m2-owner-chat`. This supersedes the owner-chat gap and blanket no-M2 authorization below only for that increment. M1's recorded results and acceptance status remain historical evidence; reviewed conversation handoff is still planned.

See the [maintained visual roadmap](roadmap.md) for the cross-milestone status, dependencies and online-sharing-first priority. Update it alongside this backlog whenever an increment, validation finding or acceptance decision changes.

Status: ready for owner review on `prototype/m1-local-sharing`, not yet accepted. M1 is authorized; M2 is not. The owner configured the purpose-specific OpenAI route and real evidence conversations and conversational verification have run. See the limited [live validation record](validation/m1-live-model.json), including failed attempts and remaining limitations.

**Owner review finding (2026-09-17):** The owner identified that this prototype provides share administration and collaborator chat but no owner project chat or selected prior-conversation handoff. The owner confirmed the intended full journey: work in an owner project conversation, review a checkpoint and its resources, then continue in an independent collaborator session with approved background. This is recorded as the planned first M2 workstream in [M2.1](m2-owner-workspace.md). Current Owner **Conversations** only displays collaborator histories; it must not be presented as owner chat. The finding does not retroactively change M1's recorded tests or imply its acceptance.

## Sprint goal

**Delivery-mode priority:** This milestone serves **online sharing**, the first of the [two intended delivery modes](product-design.md#delivery-modes). Complete its acceptance and subsequent authorized private-pilot work before implementing independent handoff. Export packages and collaborator-side import/reconstruction remain deferred; documenting them does not add them to this sprint or authorize M2.

An owner explicitly selects synthetic experiment evidence, reviews the frozen bytes and action definition, and hands a new session to a local reviewer. The reviewer asks an unscripted evidence question, follows a citation, and changes an approved evaluation seed to obtain an actual isolated run.

## Ordered backlog

| Increment | User story and acceptance | Denial/failure checks | State |
| --- | --- | --- | --- |
| 1. Reviewed snapshot | Owner selects files, reviews their exact copied contents, purpose and action, and approves the displayed digest. Later source edits do not change it. | Reject traversal, links, special/oversized files, unknown fields and mismatched approval digests. Excluded content is absent from the version. | Implemented; focused tests and owner browser path passed |
| 2. Independent review | A local reviewer starts a fresh session, sees its project/version and allowed actions, searches evidence and opens numbered lines. | Missing credentials, forged authority/history, other recipients' sessions and unapproved evidence fail at the API. | Implemented; API and browser checks passed |
| 3. Evidence conversation | A configured real model answers an unscripted question and follow-up with resolvable source ranges. Destination and data categories are visible. | No configuration means unavailable; excluded evidence cannot be retrieved; invalid references are rejected; bounded turns and requests. | Real evidence answer and concise follow-up completed; citations opened in browser. Two other attempts failed; limited reliability evidence only. |
| 4. Verification and review | Reviewer runs the fixed evaluation with an allowed seed, sees actual output and provenance, and asks the agent to compare it with the recorded baseline. | Inspect-only sessions cannot execute; invalid arguments, duplicate/conflicting submissions and exhausted budgets are checked outside the model. | Manual and conversational seed-23 execution completed with actual output and cleanup. Owner review and configured-service denial checks passed. Awaiting owner milestone acceptance. |

For each increment: specify acceptance, implement a thin usable path, run focused checks, inspect the result, then update this table and any discovered risks. Report progress throughout. Stop for owner review after M1; do not silently expand into M2.

## Technical choices and boundaries

- Python, FastAPI, Pydantic, SQLite and a locally bundled React interface. One loopback service; no CDN, hosted frontend, public tunnel or cloud deployment.
- An explicitly labelled **synthetic local demo** with local capability credentials, not OIDC identity or a private pilot. Credentials distinguish local demo actors; they do not prove a named recipient's identity. M2 must replace this mode before inviting external users.
- A fixed synthetic source project. No personal workspace selection or original agent history. An importer copies only explicit regular UTF-8 files; the approved SQLite version stores actual bytes and digests, not live source paths. This is a file snapshot, not captured process memory.
- The agent receives scoped evidence and typed service operations. It has no generic shell, filesystem or runtime-management tool. Fixed synthetic execution remains in the M0 development container profile, clearly distinct from the separately tested Linux/Kata spike and an integrated pilot executor.
- External inference requires an explicitly configured provider and purpose-specific credential. Model integration must not borrow credentials from another application. No model reply is simulated when configuration is missing.
- Local security events contain bounded identifiers and outcomes. Optional research measurements can be disabled. Application state necessarily retains selected synthetic evidence and review conversations locally; no remote analytics or trace export.

## Definition of done

The usable increment meets its acceptance criteria; relevant normal and unauthorized paths pass; errors are understandable; generated content is rendered inertly; documentation distinguishes real implementation, tests with mocks, unavailable checks and future work. M1 additionally needs an actual model conversation, actual bounded runtime execution, and a browser walkthrough. Limited tests do not prove absolute security.

**Proposed agent-quality refinement (2026-09-17):** Following owner feedback, [B0/B1 in the behavior plan](agent-behavior.md#4-proposed-delivery-increments) propose a repeatable behavioral baseline followed by evidence/provenance, action-selection and recovery improvements. The four implemented increments above establish the demonstration, not mature behavior or research readiness. Review the added acceptance scope before implementation; these proposed items have not been built or measured. M1 remains unaccepted, M2 remains unauthorized, and existing model budget limits continue to apply.

## Run the current increment

**Current interface (2026-09-17):** The owner-requested [interface refresh](ui-refresh.md) is implemented and awaiting review. Owner opens on **Shared projects**; choose **New share** to prepare a version. Reviewer opens on **Conversation**, with separate **Sources**, **Verification** and **Access & details** destinations. References to the earlier "Runs" tab below now mean **Verification**. Evidence opens in a side panel and revocation has an explicit confirmation. See the linked walkthrough and fresh UI verification record; no live model calls or budget changes were made for this revision.

**Sequencing clarification:** The earlier proposed B0/B1 refinement is not a mandatory extra milestone before M2. The current requested work is interface acceptance. After resolving M1 review, M2 remains the next major milestone to plan and approve; the behavioral baseline and improvements can be scoped into that proposal. Neither this clarification nor the UI revision authorizes M2.

From the repository root:

```sh
uv sync --no-editable
cd frontend
npm ci --ignore-scripts
npm run build
cd ..
uv run --no-editable prism demo
```

Open the printed owner link. Keep the four evidence files selected and leave `private-notes.txt` excluded. Freeze the selection, open the files and fixed executable, and approve the exact version. Open the reviewer link to start a fresh session. The service binds only `127.0.0.1:8765`; do not expose it through a proxy or tunnel. State is stored in the ignored `.prism-demo/` directory; local credential links rotate on restart. Use separate browser profiles for reviewer and observer, because each profile has one reviewer cookie.

`uv run --no-editable python -m unittest discover -s tests -p test_sharing.py -v` checks the snapshot and API boundaries. These tests use an in-process HTTP client and real temporary files/SQLite, with no model requests or runtime executions. The existing M0 runtime checks are separate. Use `--measurements` to opt in to numeric local research records; raw evidence and history are application state, not measurement exports.

To execute verification, Docker Desktop (or the documented local development engine) must be running with the pinned M0 image prepared. If needed, explicitly prepare it with `uv run --no-editable prism runtime prepare`. The reviewer Runs tab launches the real fixed evaluator. A controller subprocess receives only the local engine socket, a bounded seed and approved executable digest; it does not inherit application environment variables or model credentials. The task container has no source mount. No GCP instance is needed or started for this development mode.

## Real model configuration

The implemented optional route is OpenAI Responses at `https://api.openai.com/v1/responses`, pinned to `gpt-5.4-mini-2026-03-17`. Enabling it sends reviewer questions, reviewed purpose, current-session history, permitted evidence fragments and authorized tool results to OpenAI. Local task execution is not local inference. No hosted web/shell tools, provider conversation state, provider fallback or trace exports are enabled.

The owner configured this route for the recorded synthetic validation on 2026-09-17 (2026-09-18 UTC). Credentials remain in the ignored local state directory and are not part of the repository. A fresh checkout remains unconfigured.

Only after the owner has approved this route, create a Prism-specific credential and enter it in the local terminal, not chat:

```sh
uv run --no-editable python scripts/configure-openai.py
uv run --no-editable prism demo --allow-openai --openai-key-file .prism-demo/prism-openai.key
```

Stop the existing demo before restarting with that configuration. The helper uses hidden terminal input, writes a new owner-only key file and makes no network request. It never overwrites an existing credential. The service reads only the explicitly named file; no unrelated environment API key or other application's authenticated session is reused. The SDK's ambient custom-header route is disabled within the demo process, unrelated credential fields are explicitly empty, and environment proxy routing is disabled.

The application reserves $0.05 before each actual provider dispatch, with no refunds for uncertain outcomes, up to the explicitly configured total (default $1) per demo database. Each turn permits at most four requests/eight tool calls, each request body is at most 32 KiB, and each response allows 1,500 output tokens. A session has at most 12 turns; a turn has a 90-second deadline. The conservative reservation uses the [documented standard GPT-5.4 mini rates](https://developers.openai.com/api/docs/pricing) ($0.75 per million input tokens, $4.50 per million output tokens, checked 2026-09-17). This is a local application allowance, not a provider-enforced account spending cap. Restarting preserves reservations; creating another data directory creates a separate allowance and must not be used to evade the agreed test budget.

The owner subsequently requested more allowance after exhausting the initial $1. The current local demo is explicitly started with a **$2 total**, retaining the previously reserved $1 and adding $1 of capacity. Reproduce this authorized configuration with:

```sh
uv run --no-editable prism demo --allow-openai --openai-key-file .prism-demo/prism-openai.key --model-budget-cents 200
```

`--model-budget-cents` is a trusted operator startup option, not a reviewer or model tool. Its default remains 100 cents; use the explicitly approved total on subsequent restarts. The UI shows the configured total and remaining reservations, refreshes them after conversation requests, and provides **Refresh allowance** for changes from other sessions. Raising the total does not clear usage, change per-turn limits, recharge an API account or enable auto-recharge. Further increases require owner authorization.

Requests use `store=false` and no background mode. This does **not** promise zero provider retention: application state, abuse monitoring and provider-specific controls are distinct; see [OpenAI's data controls](https://developers.openai.com/api/docs/guides/your-data). Local deletion cannot recall content already sent to a provider.

The last permitted request in a turn has no ordinary tools available, preserving an opportunity to return a bounded answer. Incomplete provider output and invalid structured responses are rejected, with safe fixed error categories; raw provider error bodies are not displayed. These controls bound work rather than guarantee a successful answer. A failed answer can follow a completed execution, so inspect Runs before asking for another run.

## Acceptance walkthrough

1. In Owner, leave the excluded synthetic note unchecked, freeze the other four files in Verify mode, inspect the exact contents/executable and approve the displayed digest. Both local demo actors are explicit: Reviewer gets the selected mode; Observer gets Inspect.
2. Open Reviewer and start a fresh session. Search for `mean` and open `baseline.json`. References show a version, content hash and numbered lines. Search for `PRISM_UNSHARED`; the excluded note must produce no result. Direct API denial tests also check its actual evidence ID, rather than relying on search text alone.
3. In Runs, execute seed 23. Expect a new completed run with mean `0.0225`, interval approximately `0.0025` to `0.04125`, actual image/program/output identities, exit status, elapsed time and confirmed container removal. The recorded seed-7 baseline is labelled separately. Invalid arguments and Inspect execution must fail at the API.
4. After real model configuration, ask an unscripted evidence question and a follow-up. Open its citations, request a different allowed seed in natural language, then inspect the actual run and comparison. Start another session; it must not inherit the earlier conversation. **This path has now been exercised with the real model; see the live record for failures and qualifications.**
5. Submit an additional-access request. Owner can inspect the pending description and session. It grants nothing and never replays a denied action. M1 can share extra content only through a newly reviewed version, not in-place permission expansion.
6. Owner can inspect activity/conversations and revoke a version. Subsequent API evidence/run/history reads and new work fail. The UI may retain previously delivered content; revocation cannot erase what the reviewer already saw.

## Verification record and iteration findings

- `uv run --no-editable python -m unittest discover -s tests -v`: **58 passed**. This includes 33 preserved M0 checks, 13 snapshot/HTTP checks, eight agent/transport checks and four job-policy checks. Agent tests use explicit FunctionModel/HTTP doubles and cannot establish actual model usefulness.
- `uv run --no-editable python scripts/m1-runtime-check.py`: **11 real-path checks passed**, using two real container runs, with no model calls. [Recorded evidence](validation/m1-development.json) includes seed 7 and seed 23 outputs and cleanup confirmations.
- The browser walkthrough passed manual freezing, exact-version approval, a fresh reviewer session, evidence opening/search, excluded-canary search and a real seed-23 execution. A local additional-access request appeared in the owner view without granting rights. After owner revocation, another browser evidence read was denied. A second approved version was left ready for manual review. Conversation availability is explicitly disabled without configuration.
- Frontend build passed with locally bundled React 19.3.0 and esbuild 0.28.2. Python lockfile records FastAPI 0.141.1, PydanticAI 2.44.0, OpenAI 3.14.1 and Uvicorn 0.53.0. No third-party script/CDN is loaded by the interface.
- Integration checks caught the installed framework's property-style usage accessor and the SDK's ambient header behavior; both were corrected and covered before enabling any real provider request.
- The initially blocked model decision did not block evidence/UI/runtime work. The later authorized live validation is recorded below; no M2 deployment or identity work was started.

### Live validation and final M1 iteration

The [live record](validation/m1-live-model.json) preserves five real turns: three completed and two failed. The completed path includes a Chinese evidence answer, a natural-language seed-23 execution and comparison, and a concise provenance follow-up. The browser opened a cited baseline at its actual numbered lines, showed the completed run and cleanup, and displayed the conversation and failures to the owner. Eighteen recorded checks include nine direct HTTP checks against the model-enabled service; these require no extra model calls.

The first failed answer had already completed a seed-7 run. It was retained, not silently rerun on restart. An intermediate answer incorrectly described that session rerun as the original baseline source. Prompt guidance now distinguishes recorded evidence from session reruns and discourages unsolicited execution; the final concise follow-up identified the baseline file separately from seed 23. Prompt guidance is not an authorization mechanism or a semantic correctness guarantee. A longer provenance follow-up also failed. Earlier generic errors do not establish the exact causes, so neither failure is retrospectively labelled as a proven provider or quota error.

The iteration reserved the final request for an answer, encouraged concise structured output, and added safe error categories for request limits, provider rejection, incomplete output and invalid structure. `uv run --no-editable python -m unittest discover -s tests -p test_agent.py -v`: **11 passed** on the final changes, including three additional focused cases. The previous 58-test and 11-runtime-check records above remain historical results; unrelated suites were not rerun for this adapter change.

At the end of validation, 18 actual dispatches had reserved **$0.90 of the $1 local allowance**, leaving **$0.10**, or two further dispatches. This is not the provider bill; failed-turn token usage is not available. All reservations survive restart, and no budget increase or reset was performed. Check the remaining allowance before another conversation: a single question may need several dispatches. Evidence browsing, manual verification and owner controls do not consume this model allowance.

M1 is ready for the owner's local-demo review with these limitations, not accepted on the owner's behalf. The next proposed milestone is authenticated private collaboration with the integrated Linux runtime and lifecycle controls; it requires separate approval.

## A concrete first-use scenario

You are a researcher handing a small paired-method comparison to a supervisor. The supervisor wants to know whether method B's apparent advantage survives a different bootstrap seed, without receiving the researcher's preparation note. All eight observations and notes are synthetic; this is not a scientific finding.

1. Open the current Owner capability link printed by the service. Keep `overview.md`, `observations.csv`, `baseline.json` and `limitations.md` selected; leave `private-notes.txt` excluded. Use Verify access and the purpose: `Supervisor review: check whether method B's advantage on eight synthetic paired observations is stable under a new bootstrap seed.`
2. Select **Freeze selected content**, inspect the copied files and executable, then **Approve this exact version**. Note the version prefix.
3. Select **Open reviewer**. If a previous session appears, choose **Start another fresh session**, then start the handoff with your purpose and version. This preserves the old review history without inheriting it.
4. In Conversation, ask a narrow question such as: `Using only baseline.json, what are the sample count and mean difference? Cite the file and do not run an experiment.` Expect eight pairs and a mean difference of 0.0225, with a clickable citation. A typical successful route needs one evidence read and one final answer, but model behavior may consume more; the remaining validation allowance is small.
5. In Runs, use seed **23** and select **Run approved verification**. Refresh runs if needed. Expect mean 0.0225 and interval approximately 0.0025 to 0.04125, alongside the recorded seed-7 baseline. The displayed interval is rounded. Manual execution needs no model calls. With sufficient authorized model allowance, the same task can instead be requested in natural language.
6. Select **Request additional access** and ask to review whether the preparation note should be included. Submit it, then refresh Owner. It appears as pending; the note does not become accessible. Adding it requires a newly reviewed version.
7. In Owner, revoke the version you just created. In the reviewer session, try opening another evidence file. Subsequent reads are denied; content already delivered may remain visible.

Do not use a new data directory to reset the model allowance. An increase needs a separately approved budget change. Local demo actor links are not invitations to external collaborators.

### Authorized allowance adjustment validation

After the owner requested more allowance, the focused agent suite passed **12 tests**, including preservation of existing reservations when raising the total, the unchanged four-dispatch turn cap, denial at the new total, persistence after restart, and rejection of invalid limits. The sharing/API suite passed **13 tests** and the local frontend build passed. No paid model call is needed to validate a budget configuration change; verify the configured total and remaining reservation through the authenticated state endpoint and browser.

## Limits that matter for review

Only a fixed synthetic source catalog is supported. Verification accepts only the original synthetic observation/baseline/limitations bytes and the reviewed packaged evaluator; editing those inputs requires future action packaging support. The prepared file snapshot is a logical immutable application record, not protection against an administrator rewriting the database. Owner preparation and session services are trusted code in one local process; the model can reach only scoped registered tools. The trusted execution controller runs separately, while untrusted task execution is restricted by the M0 development container configuration.

All normal reads recheck session/recipient/version/expiry. One-hour demo sessions, six runs per session, 24 runs total, one active job, 24 versions and 48 sessions bound local use. If a restart finds unfinished work, it marks the job uncertain and blocks new execution; no automatic replay or claim of crash-safe reconciliation is made. The real-job check validates post-revocation delivery denial; it does not establish pilot lease behavior or a rigorous in-flight revocation latency guarantee.

Local security events retain the newest 1,000 bounded records; optional numeric measurements retain 2,000. Review duration is candidate-creation-to-approval time, not a validated measure of all preparation effort. Questions/approved answers remain local application state so the owner can review them; hidden reasoning is not stored. Full retention controls, durable audit and reliable research outcome labels remain M2 work.

## Deferred to M2 or later

Named OIDC identity, remote invitations, HTTPS hosting, integrated Kata worker, renewable host leases, crash-safe process reconciliation, permission expansion approval, retention/deletion controls and the complete EVAL-01 through EVAL-12 pilot gate. No public release or security assurance is implied by the local demo.

## Implementation references

The implementation uses [FastAPI](https://fastapi.tiangolo.com/tutorial/), the basic [PydanticAI Agent](https://pydantic.dev/docs/ai/core-concepts/agent/) and [React's documented standalone build approach](https://react.dev/learn/build-a-react-app-from-scratch). Dependencies and tested versions are recorded in lockfiles. These components do not provide Prism's authorization boundary by themselves.
