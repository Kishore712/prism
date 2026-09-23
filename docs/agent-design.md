# Prism Agent Implementation Design

- **Status:** Proposed implementation design; no agent, tool, model integration, or isolation behavior is implemented or validated by this document.
- **Date:** 2026-09-17.
- **Scope:** The P0 collaborator agent for evidence questions and approved verification.
- **Relationship:** Additive companion to the [product design](product-design.md), especially its [authorization](product-design.md#authorization), [architecture](product-design.md#system-architecture), and [release gates](product-design.md#evaluation-and-release-gates). The existing specification remains the product baseline.
- **Approval:** Permission to document this proposal does not authorize implementation. Framework choices below are recommendations for review, not approved architecture decisions or installed dependencies.

**Delivery priority:** Implement the fresh agent for online sharing first. The [two-mode product addendum](product-design.md#delivery-modes) also defines a deferred independent handoff; its agent initialization and authority differences are summarized [below](#agent-delivery-modes). This does not expand the active milestone.

**Behavior refinement proposal (2026-09-17):** The owner emphasized sustained work on agent behavior and its potential research role. The [dedicated behavior plan](agent-behavior.md) adds observable contracts, a proposed M1 regression/refinement pass, M2 collaboration behavior and later controlled experiments. A working model/tool integration is the starting point. The plan distinguishes collaborator behavior from future owner-side preparation assistance and does not claim either research novelty or approval for expanded implementation.

**Reuse preference:** Build on an established agent foundation rather than recreating its generic runtime. The [framework reuse assessment](agent-behavior.md#reuse-an-established-agent-foundation) recommends retaining the current PydanticAI foundation for the first behavior iterations, evaluating LangGraph only for a demonstrated orchestration need, and considering a coding-agent SDK for later scoped continuation. No migration or expanded tool authority is implied.

**Owner agent clarification (2026-09-17):** The owner confirmed a native project-working agent in addition to the collaborator agent. See the [new role and context addendum](#owner-working-agent) and [M2.1 implementation proposal](m2-owner-workspace.md). The original P0 sections describe the collaborator's boundary. A fresh collaborator session may inherit reviewed background; it must not inherit the owner's authority or unselected history. This is a confirmed direction with planned implementation, not a delivered capability.

## 1. Agent responsibilities

Build a handoff review assistant that can locate evidence, answer follow-up questions, request approved verification runs, compare their results, and explain missing information or capabilities. Use an existing model through an explicitly configured endpoint. Model training is not a prerequisite.

The agent decides which permitted evidence or action might help with a request. Prism decides whether the operation is authorized and the host enforces execution constraints. Model confidence, natural-language approval, tool visibility, and a successful response are not authorization evidence.

P0 supports Inspect and Verify. Free-form editing, arbitrary shell access, autonomous permission changes, unrestricted web/MCP tools, source writeback, and general-purpose coding-agent replacement remain outside this implementation.

The reference task is a CPU-only synthetic experiment review: explain the evidence for a reported comparison, rerun its approved evaluation with a different allowed seed, and distinguish the original result from the new run. A real model and real execution are required to demonstrate these capabilities; scripted answers and replayed outputs must be labeled as test fixtures.

## 2. From existing work to a fresh agent

The first importer accepts explicit files and owner-reviewed context. It does not attach a recipient to the owner's original agent process, private conversation, memory, or credentials. Integrations with particular source agents can later produce the same reviewable input format.

| Handoff component | Purpose | Review requirement |
| --- | --- | --- |
| Task and progress | Explain the review objective and current state | Owner approves the recipient-facing text |
| Findings and evidence references | Connect reported conclusions to files and prior results | Identify the source version and distinguish reported findings from hypotheses |
| Decision notes and limitations | Preserve relevant choices, assumptions, and caveats | Curated explanation only; no automatic export of hidden reasoning or full private history |
| Included files | Supply evidence and executable inputs | Explicit inclusion and exact-byte preview before approval |
| Runnable definitions | Describe fixed actions and bounded parameters | Approve executable/image identity, inputs, output scope, and resource requirements |
| Open questions | Suggest useful follow-up work | Suggestions do not grant additional permissions |

Freeze the candidate before final review. Bind the approved version to file digests, context, executable definitions, dependency-image identity, and required runtime capabilities. Grants, policy revisions, expiry, and remaining budgets stay in current control state, outside the immutable package.

Every recipient receives separate history, task state, evidence access, and execution storage. A fresh agent means a fresh authorized session, not a new model training run or a separate copy of model weights. A recipient can start another independent session against the same permitted version without inheriting another session's conversation or outputs.

Treat all content readable by the recipient runtime as potentially disclosed, including dependency-image contents, filenames, metadata, caches, and logs. A read-only dataset is still readable. The owner and authorized host administrator remain within the trust assumptions stated in the product design.

## 3. Recommended implementation and component ownership

Use Python with the basic PydanticAI Agent, typed tools, and model-provider integration, alongside the proposed FastAPI/Pydantic application stack. PydanticAI supplies the model/tool loop and typed interfaces; Prism retains authorization and durable application state. The framework documents [agent execution](https://pydantic.dev/docs/ai/core-concepts/agent/) and [function tools](https://pydantic.dev/docs/ai/tools-toolsets/tools/); neither establishes Prism's security properties.

| Component | Responsibility | Access boundary |
| --- | --- | --- |
| Session service | Authenticate requests; load version, history, and current control state | Browser supplies new user text and request identifiers, not authoritative history or roles |
| Context builder | Assemble reviewed context and permitted evidence | No source-project access or private retrieval index |
| Agent orchestrator | Run bounded model/tool iterations and produce answer candidates | Only registered Prism tools; no generic filesystem, shell, or runtime-management tool |
| Tool gateway | Validate arguments, authorize operations, reserve budgets, record decisions | Server-derived identity and version; every operation checks current policy |
| Execution worker | Launch, account for, cancel, and clean up approved jobs | Receives narrow job descriptions; runtime management is a trusted host function |
| Result service | Store artifacts and resolve citations and run records | Authorize every read and download; generated content is untrusted |
| Model adapter | Call the approved destination with scoped inputs | Provider credentials stay in trusted configuration and out of model-visible/tool-visible data |

The orchestration code may run in the trusted application service. Untrusted project code runs only in the selected sandbox. The orchestrator must not gain access to the private preparation filesystem, runtime control sockets, or a general host command interface. Shared immutable agent definitions are acceptable; mutable histories, dependencies, response buffers, and caches must not cross sessions.

Use a single configured provider initially. Do not enable provider fallback, hosted conversation storage, tracing exports, built-in web tools, shell tools, or automatic memory integrations by default. Record and test the chosen framework version before adopting it; this document does not pin an untested version.

## 4. Context and memory

Construct each model request from:

1. Prism's fixed behavioral instructions.
2. The reviewed handoff context and a server-generated summary of currently available capabilities.
3. This recipient session's server-stored conversation history.
4. The permitted evidence fragments and tool results needed for the current turn.

Label imported documents, comments, source-project instruction files, prior agent messages, and tool output as data with provenance. Do not promote their contents into system instructions or execute embedded setup commands. Delimiters, screening, and refusal prompts can assist behavior, but do not enforce the filesystem, tool, or disclosure boundary. OWASP describes both indirect injection and the limits of prompt-oriented defenses in its [prompt injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).

Start with a small approved file catalog and scoped text search. Add an embedding index only after a measured retrieval need; index only the permitted version and treat an external embedding service as an additional data recipient requiring approval. Never search a private/global index and rely on later filtering.

Persist history on the server with recipient, session, and version bindings. Do not accept client-supplied system messages, tool outcomes, approval flags, provider thread IDs, or another session's history as authoritative. PydanticAI supports explicit [message-history management](https://pydantic.dev/docs/ai/core-concepts/message-history/); Prism must supply its own trusted session history.

History compaction may summarize only that session's permitted material, preserving provenance and uncertainty. Summaries are never permission records. If scope is reduced, prevent further use or delivery of affected history, summaries, caches, pending responses, and provider-side state. P0 should stop the affected session and rebuild from the reduced authorized material rather than attempt semantic redaction of arbitrary old history. A new share version requires explicit session/version binding and does not silently enlarge an existing session.

## 5. Initial tool contracts

These names and parameters are proposed interfaces, not callable tools in the current repository. Identity, share version, policy revision, and session scope are injected by trusted services, not chosen by the model. All inputs have finite size limits; unknown fields and invalid types are rejected.

| Tool | Model-supplied input | Returned information | Enforcement |
| --- | --- | --- | --- |
| `search_evidence` | Query and bounded result count | Approved evidence IDs, snippets, and source locations | Search only the bound version and permitted session outputs |
| `read_evidence` | Evidence ID and bounded line/record range | Exact source fragment with digest and location | Resolve IDs server-side; never accept an arbitrary host path |
| `submit_verification` | Approved action ID and typed parameters | Run ID and durable queued/running status | Validate current rights and parameters; reserve budget; use a fixed executable and argument vector |
| `get_run` | Run ID | Authorized status, bounded logs, metrics, and artifact references | Check recipient/session scope again; no access through guessed identifiers |
| `request_access` | Purpose and a bounded description of the needed capability | Pending request ID and status | Creates a proposal only; does not enumerate hidden resources or grant access |

For the demo action, the seed is an integer within the owner-approved range. The model cannot choose an executable, image, mount, environment variable, network destination, output path, or new command-line flag. Construct arguments without a shell, validate option semantics as well as types, and use fixed input/output locations. This applies OWASP's distinction between command injection and argument injection in its [command execution guidance](https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html).

Only show tools useful under the current grant, while still authorizing all direct calls independently. An Inspect session cannot run verification even if it reconstructs the tool's name or API request. Download permission remains separate from merely returning an artifact ID.

An allowed request within an existing grant need not interrupt the owner again. Expansion requires the owner's authenticated approval of the exact recipient, version, action/resource, duration, and budget. Browser claims and framework approval objects are insufficient. PydanticAI explicitly documents that its [deferred-tool approval mechanism is not an authorization boundary against an untrusted client](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/).

Approval of an expansion does not replay an old pending action automatically. Recheck the active session and policy, then require a fresh execution request under the resulting grant. New content requires a newly reviewed version.

## 6. Bounded execution loop and durable state

The intended turn lifecycle is:

`Received -> Authorized -> Gathering evidence -> Responding`

or, when needed:

`Gathering evidence -> Queued verification -> Waiting for run -> Reauthorized -> Responding`

A turn may instead finish as `Needs access`, `Insufficient evidence`, `Failed`, `Cancelled`, or `Budget exhausted`. These are proposed application states. A submitted job is not a successful verification, and a model's claim that a job completed cannot change its state.

For each turn:

1. Authenticate the recipient and load current grant, version, expiry, session status, and remaining budgets.
2. Assemble only authorized context and tools. Reserve a finite model-request allowance before sending data to the approved endpoint.
3. Run the model. Validate each proposed tool call and check its authorization independently before any side effect.
4. Persist accepted jobs and their idempotency keys before dispatch. Reusing a key with different parameters is rejected. Concurrent attempts cannot spend the same budget.
5. Store tool results with provenance; recheck authorization before adding them to another model request or delivering them to the browser.
6. Validate the answer structure and reference targets, then record completion and usage.

Long jobs run outside web requests. Persist session/turn/run associations so a browser disconnect does not lose status. Resume only from server-held state after reauthorization. Do not retry uncertain execution side effects automatically after a timeout or service restart; reconcile with the worker first. Same-session turns are serialized initially to avoid racing mutable history.

Configure finite limits on model calls, tool calls, context size, output size, retries, wall time, and aggregate session use. PydanticAI's usage controls can bound the local loop, but SDK counters or post-response billing estimates do not constitute atomic aggregate reservations or guaranteed provider-side spending caps.

A revoked or expired grant blocks new model requests, tool calls, reads, and result delivery. Cancel pending generation where possible, discard late responses, terminate affected jobs, and close streams. Already transmitted provider requests and already disclosed recipient data cannot be recalled. Apply the product design's renewable-lease targets and verify whole-job termination on the actual runtime.

## 7. Answers, evidence, and UI behavior

Return a structured answer envelope with these conceptual fields:

| Field | Meaning |
| --- | --- |
| `answer` | User-facing explanation |
| `claims` | Statements classified as reported findings, new run results, or interpretations |
| `citations` | Server-resolvable evidence IDs, version/digest, and valid source ranges |
| `run_references` | Actual run IDs and result artifacts supporting verification statements |
| `limitations` | Missing evidence, failed checks, uncertainty, or unsupported requests |
| `pending_request_id` | A specific access proposal, when one was submitted |

Resolve reference IDs through the result service instead of rendering model-invented URLs. Check existence, version, scope, and ranges before display. Invalid citations must be rejected or explicitly marked unsupported; any model correction attempt consumes the same bounded budget. Structural citation validity does not prove that the evidence entails the claim or that the experiment is scientifically correct.

Show source results separately from newly generated results. A run record includes the approved version, executable/image identity, validated inputs, timestamps, actual exit status, and output digests. The UI must let a nontechnical reviewer open the cited evidence and understand what was run.

Render text and generated artifacts without active scripts or automatic remote-resource loads. Do not expose internal source paths in errors. Distinguish missing evidence, missing dependencies, insufficient permissions, unavailable host, failed execution, and model-provider failure. Missing provider configuration is an unavailable capability, not an occasion to generate a simulated answer.

## 8. Model routing, privacy, and measurement

Before enabling a model route, display the endpoint/operator, model identifier, and data categories that can be sent: recipient questions, reviewed handoff context, permitted conversation history, retrieved evidence fragments, and authorized tool results. Keep credentials out of prompts, artifacts, logs, and recipient execution environments. Model routing cannot be changed by a tool result or imported instruction.

Local tool execution does not imply local inference. Local inference is optional only when an approved compatible endpoint is available. Do not inspect unrelated personal credentials or assume access to another application's authenticated model session. Document provider-side storage and retention separately from Prism's local deletion policy.

Collect optional local research measurements for preparation/review time, user actions, successful evidence questions, verification outcomes, missing evidence/dependencies, denials, owner interventions, model usage, and runtime cost. Default fixtures are synthetic or explicitly approved. Disable remote telemetry and raw-content exports by default. Necessary task state and minimal authorization events are separate from opt-in research data. Do not collect hidden model reasoning.

## 9. Incremental delivery and acceptance

These agent capabilities fit within the existing M1/M2 roadmap; they do not replace host validation, immutable materialization, identity, or the full release gates. Each row is a separate user acceptance boundary. Approval for one row does not authorize the next.

| Feature | Dependencies | Positive acceptance | Boundary/failure acceptance |
| --- | --- | --- | --- |
| A1: Evidence conversation | Approved version, scoped evidence service, session authorization, approved real model endpoint | Ask an unscripted question and follow-up; inspect correct references to real permitted evidence | Missing evidence is acknowledged; forged history, unauthorized IDs, and injected document instructions cannot expose excluded material |
| A2: Conversational verification | A1, validated isolation adapter, typed runnable, durable queue, resource reservations and cancellation | Request a different allowed seed in natural language; obtain a real run and a sourced comparison | Invalid arguments and unapproved commands fail; duplicate requests do not duplicate work; source stays unchanged; revoke and budget exhaustion prevent continued execution/delivery |
| A3: Bounded access requests | A1 and the owner-authenticated approval/version workflow; A2 for execution-related requests | Submit a specific need, inspect the proposed increment, approve or decline, and make a fresh request under the resulting rights | Pending/declined requests do not execute; forged or replayed approvals fail; new files never appear in an old session automatically |

Start development with a small synthetic project containing approved evidence, a deterministic evaluation, and excluded canary files/context. Include follow-ups that were not used when preparing the package. Test two identities and two sessions, not only a single happy-path conversation.

Check tool-side effects and exact context assembly as well as answer text. Test denied direct API/tool calls without relying on the model to refuse them. Mocks are useful for repeatable unit checks but cannot establish actual model usefulness, sandbox isolation, or working revocation. Preserve failing cases and report the tested versions/configuration. A finite passing suite is evidence about those cases, not proof of zero leakage.

## 10. Decisions to confirm before implementation

- Confirm the proposed Python/PydanticAI approach and supported package versions through a small scoped integration check when implementation is authorized.
- Obtain an explicitly authorized model destination and budget; the documentation step requires no credential.
- Validate the reference Linux host and runtime before attributing execution-isolation properties to the agent.
- Establish practical call, latency, context, and execution limits from the synthetic workload without silently raising owner/host ceilings.
- Validate that manually prepared evidence supports useful unscripted questions before adding agent-import adapters, learned scope proposals, or persistent memory features.

The project maintains product value and research novelty as hypotheses. This design is a concrete implementation proposal for testing them, not evidence that Prism is first, uniquely safe, or superior to existing products. See [product and research revalidation](product-research-validation.md).

## Implementation status update: M1, 2026-09-17

The proposed interfaces above now have a synthetic local implementation: fresh server-held sessions, version-bound evidence search/read tools, a PydanticAI agent with structured answers, validated references, a single explicitly configured model route, and a fixed verification job service. This addition preserves the original design as requirements rather than claiming every requirement is complete.

The agent/tool loop and OpenAI SDK wire contract have been checked with labelled test doubles. No live model call has been made for M1 yet; owner provider selection and a purpose-specific credential remain necessary for real conversation acceptance. The manual verification path has run real constrained containers. See the [M1 delivery and acceptance record](m1-local-sharing.md) for precise scope, commands and limitations. Remote identity, integrated Linux/Kata execution and full lease/revocation release gates remain unimplemented.

### Subsequent live validation: 2026-09-17 local / 2026-09-18 UTC

The owner subsequently configured the purpose-specific OpenAI route. Real evidence conversation, natural-language verification and a concise follow-up completed; two other turns failed and remain recorded. Source citations and run links opened in the browser. The final focused agent suite passed 11 tests; model-enabled direct API denial checks passed without additional provider calls. See the [live record](validation/m1-live-model.json) and [M1 guide](m1-local-sharing.md) for the provenance wording issue, error handling, unchanged budgets and remaining limitations. This update supersedes the earlier no-live-call status without changing the original design requirements. M1 awaits owner review; M2 and private-pilot readiness remain unapproved.

<a id="agent-delivery-modes"></a>

## Addendum: Agent behavior across delivery modes (2026-09-17)

The shared foundation is a fresh agent initialized from reviewed context and a versioned evidence catalog, with declared tools, validated references and explicit model routing. Neither mode transfers the owner's unrestricted agent process, hidden reasoning or personal credentials. Environment images, project files, agent context and active process state remain separate concepts.

In **online sharing**, the original owner's host and control service enforce current grants, file/tool/network access, runtime budgets and revocation. The collaborator receives a bounded session; its agent has no route into private preparation state. This remains the implementation priority and the scope of the existing P0 tool contracts and acceptance gates.

In the deferred **independent handoff**, the source owner approves the exact export content. The destination operator reviews its executable definitions, configures local permissions and budgets, supplies their own model credentials, and starts a fresh agent in a separate workspace. Imported context does not authorize scripts, tools, host mounts or service access. Source grants, host leases, session identifiers and provider conversation handles are not portable authority. Preserve evidence/run provenance as records, without replaying uncertain actions or inheriting live sessions.

The independent recipient controls the destination host and can modify its copy. Original-owner restrictions cannot be promised as non-bypassable there, and online revocation cannot stop offline use of exported material. Destination enforcement protects its own resources under its operator's policy. Online history and outcomes enter the package only after explicit export review; importing does not automatically merge private history or replay tools.

Validate the future imported agent with an unscripted evidence question and actual verification while the original host is unavailable, using the collaborator's configured model route. External inference may still require network access to that provider. Import, credential rebinding and destination lifecycle checks are deferred work, governed by the [delivery order and acceptance criteria](product-design.md#delivery-mode-delivery-order), not prerequisites for completing online sharing.

<a id="owner-working-agent"></a>

## Addendum: Owner working agent and approved context continuation (2026-09-17)

**Subsequent M2.1c checkpoint:** [Approved-context continuation](m2-context-continuation.md) is implemented and awaiting owner review. Fresh collaborators receive reviewed background as untrusted data, selected evidence and existing scoped tools. Background, historical results and new runs have separate validated references. No owner-private history, privileged messages, arbitrary tools or live state is transferred. Fifty-four focused tests and a live failed-answer/successful-recovery sequence are recorded; private-pilot readiness remains false. This supersedes the older blocked-activation status below.

**Subsequent M2.1b checkpoint:** Manual reviewed-context preparation is now implemented and awaiting owner review; see [the handoff guide](m2-reviewed-handoff.md). It resolves completed source messages and actual result records server-side, copies selected bytes, labels edits/gaps, rewrites attached references and approves the whole candidate digest. It makes no model call and does not infer scope. Collaborator initialization in items 4–5 below remains planned for M2.1c; saved schema-2 versions cannot activate through the legacy recipient path. This supersedes the earlier preparation-unimplemented status while preserving the design requirements.

The owner confirmed that the source of a handoff should also be usable as a native Prism project chat. This extends the scope beyond the collaborator-focused sections above. The [M2.1 plan](m2-owner-workspace.md) records implementation and acceptance. Subsequently authorized M2.1a now implements owner chat for the synthetic project; the reviewed-context initialization contract below remains planned for M2.1b/c.

| Agent role | Working context | Actions and authority |
| --- | --- | --- |
| Owner project agent | Its owner's private project conversation and explicitly configured project resources/results | Project-authorized evidence access and bounded tasks; no implicit access to the entire host or unrelated projects |
| Collaborator agent | A frozen approved handoff context, approved resources and its own new session history | Only currently granted capabilities; no query path into owner working state |

A future preparation assistant may propose context selection or dependencies for owner review. That is a separate automation feature, not the definition of the owner working agent and not a prerequisite for manual handoff. Reuse the existing agent framework and adapter for common mechanics, with distinct owner and collaborator authorization, tool registration and context construction. Reusing code does not mean sharing a live agent instance or adding an owner capability switch to a recipient-controlled tool.

### Context initialization contract

1. The owner selects a completed conversation checkpoint. Resolve source messages, file revisions and actual result records through owner-authorized services. Rendered answers and explicit decision notes may be selected; hidden reasoning and raw provider message payloads may not.
2. Materialize a recipient-facing context containing the reviewed summary, ordered excerpts, task state and share-local evidence references. Label verbatim versus edited content and reported findings versus historical run evidence. Do not silently add an entire source conversation to provide continuity.
3. Include this exact context in the candidate digest and approval preview. Source edits or later turns cannot modify the frozen version. Missing support must be reviewed as a gap; it cannot trigger private retrieval or automatic inclusion.
4. Create a fresh collaborator session with the approved context as labelled source data, separate from the collaborator's authoritative session history and Prism's fixed instructions. It is valid to display approved prior exchanges above the new composer. Do not represent them as new collaborator turns or completed actions in that session.
5. Each subsequent request uses that version's permitted context and the recipient's own history. Tools and context readers check current access. A new recipient starts a separate history; no inherited provider thread, tool-call continuation, queued side effect, credential or owner privilege is permitted.

The original instruction that the collaborator orchestrator must not access the private preparation filesystem remains in force. The added owner service has a different explicit project scope; it must not become a fallback tool or retrieval path for collaborator requests. Any summary or excerpt can contain facts derived from private files, so review its exact content as disclosure. File filtering alone cannot sanitize prior answers.

**M2.1a implementation checkpoint:** A distinct owner identity/scope and storage adapter now authorize project turns, evidence and actual fixed-action runs. Model dispatch consent and the persistent aggregate allowance are enforced outside the model. Automated checks cover cross-role/project access and absence of private owner material in assembled collaborator inputs. The [delivery record](m2-owner-workspace.md#current-delivery-record) preserves four real-model attempts, including two failed answers and one completed container run. This is a limited local demonstration, not completed handoff behavior or a reliability benchmark.

### Provenance, cost and validation

Historical owner run evidence must carry approved input, executable and output identities, use share-local references and remain labelled as a past result. A new collaborator run requires a fresh authorized job and finite budget. Imported source roles and statements about approvals remain inert content; they do not establish permission or replay the earlier call.

Owner inference can send a broader, explicitly configured project context to the model provider than the shared branch can. Disclose and authorize these data categories separately, without borrowing unrelated credentials or increasing the persistent operator allowance. Attribute usage by role/session while enforcing the same aggregate ceiling. Missing configuration or exhausted context/budget must produce an honest unavailable/limited state.

Validate owner chat first, exact checkpoint review second and contextual continuation third. Test direct unauthorized API/tool requests, excluded canaries in assembled inputs, unchanged frozen versions after new owner work, cross-session separation, non-replayed historical actions and subsequent-access denial on revoke. Real model questions and actual runs establish limited end-to-end behavior; labelled test doubles cover deterministic boundaries. No new result is claimed by this addendum.


## General-purpose handoff interface (2026-09-19)

The owner clarified that Prism is a general agent handoff product. Experimental reproduction is one validation scenario, not its primary information architecture. The authorized interface refinement makes conversation the task entry point, with shared background/resources, capabilities/permissions, and activity/results as supporting views. It removes dedicated experiment launch forms and top-level statistical dashboards.

Specific action parameters, output and execution provenance belong in the corresponding task details. Exact owner approval must still disclose the executable and its parameter/resource limits; generic presentation must not obscure what is being authorized. Imported historical results remain separate from new session activity. Failed answers and completed executions must remain distinguishable so users do not unknowingly repeat work.

This interface change does not implement arbitrary tools, project import or unrestricted execution. The existing synthetic action remains the only executable capability. Agent tool calls and direct APIs retain their server-side checks, and additional access requests grant nothing until a later approved workflow implements expansion. The product can adopt further bounded tools without adding a separate application-level screen for each tool.

See [the implementation and acceptance guide](general-handoff-workspace.md) for this increment; earlier experiment-specific walkthroughs describe historical interface checkpoints.


## Addendum: M2.2 project source selection and fixed JSON checking (2026-09-19)

The owner separately authorized M2.2 on `prototype/m2-project-import`. The operator may configure repeatable absolute paths to `.prism-project.json` through `uv run --no-editable prism demo --project ...`. The selected manifest is the source inventory: a strict schema-1 object with a slug, a title of at most 120 characters, one to eight explicit relative UTF-8 filenames, and `action` equal to `json-check` or `null`. Each filename is limited to 240 characters and eight path segments. The application records `source_kind` as `local-project` for configured local sources, including the `document-handoff` acceptance example, and `synthetic` only for the legacy paired-evaluation fixture; this is metadata rather than an extra manifest field. A parent or nested manifest cannot override it, and the importer does not recursively scan the source root.

The owner project picker selects one configured source and then explicit files, with every imported project file initially unselected for New Share. The owner/operator may read declared files to perform that selection. Private-session file choices are independent of New Share: the owner may explicitly select a private note for the owner model after consent, while collaborators receive only the reviewed share selection. A `README.md`-only selection supports owner chat inspection. A direct file-only handoff needs no model call. A private owner chat uses the existing model-consent disclosure for its selected context. A fresh collaborator agent receives no source-agent memory, hidden owner history, credentials, unselected files, or live process state. Source edits after approval cannot modify the immutable version.

The new `check_json` tool is a fixed JSON action boundary. Before an executable version is created, its base64 action payload is preflighted against a 100 KiB encoded limit. It accepts only approved selected JSON inputs and cannot turn model text into a script, executable path, package installation, setup command, network request, host mount, or source write. It operates inside the pinned existing development runtime with network disabled, no host mount, and the M2.2 limits of 128 MiB memory, 0.5 CPU, 32 processes, 10 seconds, 8 MiB scratch, and 64 KiB captured output. JSON depth is limited to 80 and integer values to 256 digits. A failed check uses `valid: false` with `error.reason` set to `syntax`, `unsupported_depth`, or `unsupported_number`. The existing bootstrap action remains fixture-only.

The 16 KiB manifest cap, 32 KiB per-file cap, and 96 KiB sum-of-selected-file-bytes version cap are separate from the action's 100 KiB base64 transport bound and parser limits. A malformed JSON document must be distinguished from syntactically valid JSON outside supported depth or number limits; both are bounded outcomes and neither permits a fallback action. The unified activity/results record identifies this new action separately from historical owner results.

M2.2 acceptance uses the new authorized English `document-handoff` example, which is a `local-project` source loaded through its manifest; the legacy paired-evaluation fixture remains `synthetic`. It does not claim behavior on private user workspaces. The [validation record](validation/m2-project-import.json) records local-development completion, zero model calls, browser evidence, and preservation audits; owner review remains pending. No live model JSON response, reference-Linux integration, or pilot readiness is claimed. M2.3 reference-Linux runtime integration follows only after M2.2 review and separate approval.
