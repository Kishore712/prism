# Prism Product Design: Secure Agent Sharing and Handoff

- **Status:** Review-ready design; implementation has not started.
- **Version:** 0.1
- **Date:** 2026-09-16
- **Repository baseline inspected:** main at daf028c (Initialize Prism repository).
- **Primary audience:** Product contributors and engineers building the first open-source prototype.
- **Scope:** Privacy boundaries, bounded continuation, owner-hosted sandbox execution, and delivery criteria.
- **How to read:** Sections 1–4 define the product, 5–16 define behavior and boundaries, 17–19 define delivery, and 20–23 provide context and decisions.

**Delivery direction clarified on 2026-09-17:** Prism has two intended delivery modes: online sharing on owner-controlled resources, followed later by independent handoff to collaborator-controlled resources. Implement and validate online sharing first. The [delivery modes addendum](#delivery-modes) defines the distinction, future handoff requirements, and ordering without expanding P0 or authorizing deferred implementation. The baseline status above is historical; consult the [M1 guide](m1-local-sharing.md) for current implementation evidence.

**Owner-workflow clarification (2026-09-17):** The owner confirmed that Prism should include an owner project agent and chat, with reviewed conversation context handed to a separate collaborator session. The [owner workspace addendum](#owner-project-workspace) extends the earlier management-only surface and existing-work assumption. [M2.1](m2-owner-workspace.md) records the implementation proposal; documentation approval is not implementation authorization or M1 acceptance.

## Contents

1. [Product definition](#product-definition)
2. [Users and jobs to be done](#users-and-jobs)
3. [Reference scenarios and end-to-end demo](#reference-scenarios)
4. [Scope and priorities](#scope-and-priorities)
5. [Core concepts and ownership](#domain-model)
6. [Authorization model](#authorization)
7. [Resource and disclosure policy](#resource-policy)
8. [User journeys](#user-journeys)
9. [Product surfaces and interaction design](#experience-design)
10. [Privacy contract and threat model](#privacy-and-threat-model)
11. [System responsibilities and trust boundaries](#system-architecture)
12. [Snapshots and owner-hosted resources](#snapshots-and-local-resources)
13. [Lifecycle, updates, and revocation](#lifecycle)
14. [Security invariants and implementation constraints](#security-invariants)
15. [Deployment and model routing](#deployment-and-models)
16. [Reliability, resource use, and performance](#reliability-and-cost)
17. [Evaluation and release gates](#evaluation-and-release-gates)
18. [Delivery roadmap and first implementation backlog](#roadmap)
19. [Open-source project plan and repository organization](#open-source-plan)
20. [Competitive positioning and evidence](#competitive-positioning)
21. [Illustrative sharing contract](#example-contract)
22. [Decisions, assumptions, and open questions](#decisions-and-open-questions)
23. [Research opportunities after product validation](#research-path)

- [Addendum: Two delivery modes and implementation priority](#delivery-modes)
- [Addendum: Owner project workspace and conversation handoff](#owner-project-workspace)
- [Addendum: M2.2 explicit project import and bounded JSON action](#m2-project-import)

<a id="product-definition"></a>

## 1. Product definition

Prism is an open-source product direction for sharing and handing off personal agent work with explicit privacy and execution boundaries. An owner creates an approved, versioned share of an existing project. Named collaborators can continue a conversation, inspect evidence, rerun permitted experiments, and perform limited follow-up work in isolated sessions.

The flagship mode runs these sessions on the owner's computer or an authorized development server. Collaborators receive an authenticated access entry, not the owner's account or a downloadable copy of the entire private environment. The owner decides what information, tools, and computing resources the share may use.

**Product promise:** make agent-assisted work easier to understand, verify, and continue, with less handoff preparation and without granting general access to the owner's workspace.

**Initial wedge:** research and development handoffs to a supervisor, team lead, or collaborator who needs useful execution capabilities but does not need full project access.

**Success condition:** collaborators finish meaningful follow-up work while owners spend less time preparing, explaining, operating, and approving. Access boundaries remain enforceable independently of model behavior.

This is a review-ready product specification, not a report of implemented capabilities. At the inspected repository baseline, Prism contains only project and contribution documentation. The repository is private and has no selected license; public open-source release is a future milestone. Security, usability, and performance claims below are requirements or hypotheses until validated.

<a id="users-and-jobs"></a>

## 2. Users and jobs to be done

| User | Primary job | What they need to control |
| --- | --- | --- |
| Owner | Hand over work without becoming the permanent explainer or operator | Disclosure, execution, cost, duration, and changes to original work |
| Reviewer | Understand a result and test specific claims | Evidence, versions, approved reruns, and clear limitations |
| Limited collaborator | Continue a bounded task and return useful changes | A writable private work area, permitted tools, and exportable deliverables |
| Host administrator | Permit delegated jobs on a machine they operate | Machine policy, resource budgets, network access, and termination |

One person may hold multiple roles. Owning an agent or having SSH access does not automatically authorize sharing the underlying server, dataset, license, or organization credentials. Host policy is an upper bound on what an owner can delegate.

Prioritize small research and engineering teams with existing Linux development environments. Supervisors and team leads are the first reviewer persona; a collaborator completing a scoped follow-up task is the second. Formal anonymous peer review, regulated-data computation, and public agent publishing are later markets.

The problem is recurrent coordination: locating evidence, explaining context, recreating dependencies, rerunning commands, and deciding which resources are safe to expose. A shared chat transcript often lacks executable context; an unrestricted working environment provides more access than the task requires.

<a id="reference-scenarios"></a>

## 3. Reference scenarios and end-to-end demo

### Scenario A: supervisor reviews an experiment

An owner has code, approved evaluation data, model weights, and results on a development server. Private notes and unrelated projects are also present. The owner selects a result and asks Prism to prepare a share for a named supervisor.

Prism proposes selected files, an evidence summary, and a fixed evaluation action with a validated seed parameter. The owner reviews the exact candidate share and assigns a time and resource budget. The supervisor asks which run supports a figure, changes the seed, and compares the new output with the original. Responses link to permitted evidence and run records. A request for an unshared training dataset produces a bounded permission request, without exposing its private location or contents.

### Scenario B: limited follow-up work

An owner permits a collaborator to investigate a failure and edit a selected code subset in a separate workspace. The collaborator asks the agent to reproduce the failure, changes approved files, runs an allowed test, and returns a patch plus test evidence. The owner explicitly reviews and applies the patch; the shared session cannot write back to the original project.

### Scenario C: two audiences

A supervisor may inspect an approved evaluation dataset; an external collaborator may use only a public sample. Each receives a separate grant and session from an appropriate share version. Private source context, approvals, and outputs from one audience do not become available to the other.

**Demo completion:** a real invited person opens a browser, asks an evidence question, triggers a permitted rerun, receives a traceable result, attempts a forbidden action that is blocked, and loses further access when the owner revokes the session. Use synthetic fixtures for the public demo.

<a id="scope-and-priorities"></a>

## 4. Scope and priorities

| Capability | P0: private pilot | P1: broader handoff | Later |
| --- | --- | --- | --- |
| Source | One selected project, explicit files, curated context | Additional agent import adapters and dependency suggestions | Broad heterogeneous workspace import |
| Hosting | Owner-controlled Linux host, local or development server | macOS/Windows host adapters after isolation validation | Managed hosting providers |
| Interaction | Evidence chat and structured approved reruns | Scoped edits and isolated follow-up work | Additional application and browser workflows |
| Identity | Named invitees, authenticated access, explicit grants | Organization groups and delegated ownership | Optional public publishing as a separate mode |
| State | Immutable approved files, dependency image, scratch workspace | Incremental versions and version-aware updates | Selective runtime checkpoints where justified |
| Compute | CPU, RAM, disk, process, time, concurrency and model budgets | Validated GPU job scheduling | Specialized hardware |
| Privacy | Explicit inclusion, candidate review, credential exclusion | Assisted scope proposals and reusable owner rules | Learned preferences, subject to owner authority |
| Outputs | Evidence references, run records, selected downloads | Reviewed patch/result return | Additional integrations |

P0 must support both evidence questions and an actual approved execution. A transcript viewer alone does not satisfy the product. Scoped free-form editing belongs to P1; it does not disappear from the product vision.

P0 excludes arbitrary remote shell access, unrestricted host mounts, automatic writeback, inherited personal credentials, arbitrary private-data computation, raw RAM migration, GPU memory snapshots, public anonymous links, and universal agent compatibility. These exclusions define the first delivery boundary.

A Mac owner can use the browser and an authorized Linux development host in P0. Running the sandbox directly on macOS or Windows requires a separately validated host adapter; do not advertise it as already supported.

The P0 scope above delivers **online sharing**. **Independent handoff** is a deferred delivery mode, not an additional P0 requirement or an already implemented export feature. Its distinction from access presets, packaging requirements and future acceptance criteria are specified in the [delivery modes addendum](#delivery-modes).

<a id="domain-model"></a>

## 5. Core concepts and ownership

| Object | Meaning |
| --- | --- |
| Source project | Private material used to prepare a share; never a collaborator endpoint |
| Share | The owner-managed handoff, purpose, recipients, and version history |
| Share version | Immutable approved content, evidence context, dependency references, and runnable definitions |
| Grant | Recipient-bound permission set, version binding, expiry, and budgets |
| Session | One recipient's conversation, isolated workspace, and execution history |
| Host | The machine that enforces execution and resource limits |
| Run | A concrete action with validated inputs, status, resource use, and outputs |
| Artifact | A result explicitly available for inspection or export |
| Approval request | A proposed, specific increase in scope; not an authorization |
| Audit event | A record of a control decision or action, with sensitive values omitted |

A share version is an application-level snapshot. It does not imply a byte-for-byte image of the owner's disk or processes. Model weights, RAM capacity, process RAM contents, agent memory files, and conversation context are distinct resources.

Separate grants may reference the same safe base version. When audiences require different content, create different versions or filtered materializations. The union of their permissions must never become the environment of either recipient.

The owner retains the source. Recipient changes belong to their session until explicitly exported and accepted. Ownership of contributed material and visitor-data retention must be explained in the collaboration agreement; Prism does not invent legal rights.

<a id="authorization"></a>

## 6. Authorization model

Permissions are evaluated using authenticated identity, share version, action, resource, current policy revision, expiry, and remaining budget. Role names are presets, not security boundaries.

Effective access is the intersection of host policy, owner grant, session scope, and remaining budgets. A deny or expired authorization blocks the action. A permission proposal, tool output, imported instruction, or model-generated rationale cannot modify that intersection.

| Preset | Typical allowed capabilities | Explicit limits |
| --- | --- | --- |
| Inspect | Chat over approved context; read approved evidence | No execution or edits |
| Verify | Inspect plus selected runnable actions and parameter ranges | No arbitrary shell or original-project writes |
| Continue | Verify plus approved file edits and tools in an isolated workspace | No source writeback or permission administration |
| Owner | Prepare shares, grant access, review exports, revoke | Subject to host and organization policy |

P0 implements Inspect and Verify. Continue is the first P1 extension. Each preset must display its actual resource scope before approval.

Invitations are single-use enrollment links bound to a named, authenticated identity, with an expiry. Possession of a URL alone does not grant access. Use an established identity provider through OIDC for the pilot; the deployment must configure one. No home-grown password system or silent anonymous fallback.

Grant increases require owner confirmation of the specific resource/action, audience, duration, and budget. Newly added source content requires a new reviewed version. Reductions apply immediately to future actions and trigger cancellation of affected work. Owners can revoke one recipient without affecting others.

Resource budgets are reserved before launch and enforced during execution; concurrent requests cannot each spend the same remaining allowance.

<a id="resource-policy"></a>

## 7. Resource and disclosure policy

| Resource | Default | Delegation rule |
| --- | --- | --- |
| Files and datasets | Excluded | Include explicit materialized paths; specify read or session-write rights |
| Conversation and memory | Excluded | Publish reviewed excerpts or summaries with source references |
| Skills and project instructions | Inert content | Review code, dependencies and tool requirements before enabling |
| Execution | Denied | P0 uses fixed runnable definitions with typed and bounded inputs |
| Network and MCP tools | Denied | Permit specific services and operations through enforcing gateways |
| Credentials | Never copied | Later brokered credentials stay outside recipient runtimes |
| CPU/RAM/processes/disk/time | No unlimited defaults | Require explicit limits and host-enforced accounting |
| GPU | Disabled in P0 | Later permit scheduled jobs on validated device configurations |
| Downloads and uploads | Only explicit download artifacts in P0 | Later uploads are isolated and quota-bound |
| Source modification | Denied | Export a patch; owner reviews and applies it separately |

Scope includes filenames, metadata, indexes, embeddings, tool results, logs, and generated outputs. A search index must be built only from the permitted version; retrieving from private indexes and filtering afterward is insufficient.

Once code can freely read a dataset, it can usually reveal that dataset through outputs. P0 therefore treats runtime-readable content as disclosed to the recipient. "Compute on private data without seeing it" requires a separately designed fixed computation service with input/output controls; it is not supplied by a prompt or a read-only mount.

An owner can forbid downloads in the interface, but cannot prevent a recipient from recording information already shown. Expiry and revocation stop future access; they do not erase prior disclosures.

<a id="user-journeys"></a>

## 8. User journeys

### Prepare and publish

1. Connect an authorized host and choose a project. Verify runtime isolation, storage, connectivity, and host policy.
2. State the purpose and named recipient. Select Inspect or Verify and intended follow-up actions.
3. Inspect the proposed files, context, skills, dependencies, and resource limits. Every inclusion has a reason and an editable decision.
4. Review the exact staged content, with private material excluded. A preview shows the recipient's accessible evidence and capabilities.
5. Build an immutable candidate; run a clean startup and approved verification action. Report missing dependencies and failures.
6. Confirm the content digest, grant, model destination, expiry, and budgets. Activate the share and issue the invitation.

No access is enabled until review and validation finish. Changes to reviewed material invalidate the approval for that material.

### Join, question, and work

1. Authenticate and accept the invitation.
2. See the task, available evidence, permitted actions, resource limits, and who can view session records.
3. Ask a question. The agent cites approved evidence and distinguishes source results from newly generated results.
4. Request a run. Prism validates inputs, reserves budget, and executes in the recipient's isolated workspace.
5. Inspect outputs and a run record; download only published artifacts. P1 additionally supports scoped edits and patch export.

### Ask for more and close

A denied request explains the permitted boundary without naming hidden resources. The recipient can submit a purpose and minimal requested capability. The owner can approve once, amend the grant, publish a new version, or decline. Pending requests never execute automatically.

On completion, the owner can receive an approved result summary or patch, revoke access, and delete session data according to retention. Source changes require a separate owner action. The UI distinguishes "access revoked," "execution stopped," and "stored data deleted."

<a id="experience-design"></a>

## 9. Product surfaces and interaction design

| Surface | Essential content |
| --- | --- |
| Owner dashboard | Shares, recipients, host readiness, active jobs, budgets, pending requests, revoke action |
| Create-share flow | Purpose, recipient, source selection, included content, action scope, preview, validation |
| Scope review | Exact file/context diff, inclusion reasons, unresolved items, dependencies, editable limits |
| Collaborator workspace | Chat, evidence browser, permitted actions, runs, results, session status |
| Run detail | Version, validated inputs, environment, progress, safe logs, outputs, comparison |
| Access request | Requested capability and purpose, incremental exposure, duration and resource effect |
| Activity and settings | Grants, changes, model routing, retention, audit export, host management |

Use ordinary language in the main flow: "Can read these files," "Can rerun evaluation," "Can edit a separate copy," and "Runs on your development server." Show implementation details only in expandable diagnostics.

Before publishing, show who can access what, where computation occurs, where model requests go, what can be downloaded, the maximum resource budget, and how to stop access. A green check means the stated checks passed; it must not imply all privacy risks were discovered.

Keep denials useful: "This session can rerun evaluation but cannot change the training pipeline." Do not reveal whether a private filename exists. Approval cards must label model explanations as proposals and show the exact system-enforced change.

Failure states include host offline, invitation expired, version unavailable, budget exhausted, dependency missing, policy changed, and run interrupted. Each state needs a recovery action that does not silently broaden permissions.

Recipient conversations and outputs are visible to the owner under the pilot policy. Explain this before joining. Do not promise private collaborator conversations. Session content is not automatically merged into the source agent's memory.

<a id="privacy-and-threat-model"></a>

## 10. Privacy contract and threat model

Protect the owner's unshared files, private context, credentials, source integrity, computing budget, and other sessions. Also protect a collaborator's session from other collaborators. The owner and authorized host administrator can access host-side data; protection from them is outside the initial model.

Consider malicious or mistaken recipients, prompt injection in files and tools, model mistakes, exposed invitation URLs, dependency code, cross-session contamination, and abuse of authorized computation. Assume the host OS, selected isolation mechanism, identity service, and enforcement components are correctly operated; document their actual limits.

| Threat | Required response |
| --- | --- |
| Prompt asks for private context | Private context is absent from recipient prompts, retrieval, and tools |
| Tool tries to read outside scope | Host-enforced filesystem boundary; no direct private-source endpoint |
| Symlink, archive or path traversal | Safe materialization and canonical resource resolution |
| Skill or MCP action reaches the host | Validate adapter permissions; deny unmanaged host execution |
| Recipient exhausts resources | Hard runtime limits, queue limits, and aggregate budgets |
| Output or error leaks secrets | Exclude secrets at source; sanitize safe operational errors; gate output delivery |
| Session A queries session B | Separate authorization, workspace, indexes, conversation state, and outputs |
| Access expires during a run | Host enforcement stops work and prevents later result delivery |

An agent proposes sharing decisions; an owner authorizes them; a policy service and host enforce them. Fine-tuning is optional future assistance, not an enforcement dependency.

File exclusion alone does not sanitize process memory, old transcripts, caches, filenames, or environment variables. P0 starts a fresh collaborator agent using only the approved version. It never attaches the collaborator to the owner's unrestricted agent session.

Privacy review detects known patterns and assists decisions; it cannot infer every unspoken redline. State unknowns clearly. An approved but sensitive file can still be disclosed within its grant.

External model providers are an additional data recipient. Local execution does not mean local inference or zero network transfer. Recipient prompts, authorized evidence, and outputs sent to a provider must follow the approved routing policy. Analytics and error reporting must not upload conversation or project content by default.

No claim of perfect isolation, zero leakage, protection from a malicious host administrator, or independently verified scientific truth is made. Verification on an owner's machine establishes an attributable rerun within that trust model.

<a id="system-architecture"></a>

## 11. System responsibilities and trust boundaries

~~~mermaid
flowchart LR
    O["Owner"] --> P["Preparation and approval"]
    S["Private source"] --> P
    P --> V["Approved immutable version"]
    C["Authenticated collaborator"] --> G["Gateway and policy service"]
    G --> H["Host executor and resource scheduler"]
    V --> H
    H --> A["Isolated session agent and tools"]
    A --> R["Authorized evidence and outputs"]
    R --> G
    G --> C
    G --> L["Access and action records"]
    H --> L
~~~

**Preparation plane:** a private, owner-authorized importer proposes content and runnable definitions. Its richer source access must not be reachable from a collaborator session.

**Control plane:** manages identity, shares, versions, grants, invitations, approval requests, budgets, and revocation. It authorizes each operation and issues short-lived execution leases.

**Execution plane:** a host agent validates leases, materializes approved versions, runs isolated sessions, enforces quotas, and terminates work. It independently validates policy revision and expiry; the chat frontend is not a security boundary.

**Model and tool plane:** the session agent receives only approved context. Tools are capabilities exposed through checked adapters. A model can propose actions but cannot mint a grant, pick an arbitrary host path, or bypass enforcement.

**Result plane:** stores session artifacts, verifies access before every read or download, separates safe operational logs from content, and records version and run provenance. All routes, including streaming and reconnects, check current authorization.

The initial deployment can place these logical responsibilities in one service plus a host worker; separate microservices are not required. Keep interfaces explicit so storage, runtime and model adapters can evolve.

A runtime adapter must expose create, run, cancel, destroy, health, resource accounting, and supported isolation capabilities. Reject unsupported policies instead of silently weakening them. A source importer and a runtime executor are different adapters.

<a id="snapshots-and-local-resources"></a>

## 12. Snapshots and owner-hosted resources

A share manifest records a version identifier; digests of included files; dependency image identity; reviewed context; runnable definitions and input schemas; required adapter capabilities; and provenance. Grants and live budgets are separately versioned controls, not frozen credentials embedded in the snapshot.

Materialize only approved content. Do not attach an entire home directory, unrestricted repository history, host sockets, SSH agent, browser profile, or Docker socket. Resolve symbolic links and hard links safely; refuse paths that escape the selection. Nested archives and binary content need explicit review or exclusion.

Freeze content before the final approval. Verify that the bytes used at activation match the reviewed digest, avoiding changes between checking and use. Large approved assets may use immutable local storage with read-only access and recipient-scoped mount rules. A live mutable source directory does not satisfy snapshot semantics.

P0 reconstructs an execution environment from approved files and a known dependency image. Reusing local images and immutable assets can avoid transfer and installation. It does not promise to capture arbitrary installed software from the owner's machine. Dependency discovery can suggest missing components; it cannot silently include them.

Use a read-only base with a private writable overlay or equivalent separate workspace. Changes and outputs never mutate the source. Caches may be shared only when their contents are approved and cannot reveal another session's work.

RAM limits describe resource capacity, not a transfer of process memory. P0 excludes raw process and GPU memory snapshots. Full runtime capture could contain credentials or private intermediate values; future support requires a separate security design.

The snapshot stays on the owner-controlled host by default. A collaborator receives a session bound to that version. Downloadable environment export is a later, explicit disclosure action whose contents cannot be recalled.

Compute is borrowed under a grant, not copied with the snapshot. Use queueing, concurrency caps, cancellation, and owner-reserved capacity. GPU device access and hard VRAM isolation depend on the selected platform; do not promise a generic enforceable VRAM quota.

<a id="lifecycle"></a>

## 13. Lifecycle, updates, and revocation

Share versions progress through Preparing → Awaiting review → Validating → Ready, with explicit Failed and Retired states. A version cannot be activated when review or validation is incomplete.

Sessions progress through Invited → Active → Paused, and may end as Completed, Expired, Revoked, or Failed. Host availability is a separate property. Runs have Queued, Running, Succeeded, Failed, Cancelled, and Timed out states. A disconnected frontend does not mean a run stopped.

Keep active sessions pinned to their approved version. Publishing a new version does not silently add its files or instructions to existing sessions. An owner can require a new session against the new version. Show the differences and re-evaluate access and budgets.

On revoke or expiry:
1. Deny new messages, tool requests, reads, downloads, and reconnects at the gateway.
2. Invalidate the grant revision and cancel queued and running work on the host.
3. Close output streams and prevent further artifact delivery.
4. Confirm termination of the entire job group, not just its parent process.
5. Delete session data according to retention and show deletion status separately.

Use renewable, short-lived host leases. P0 proposes a maximum 30-second lease lifetime and a 30-second host termination deadline after loss of authorization; these are acceptance targets, not current guarantees. If renewal fails, the host stops delegated execution rather than continuing indefinitely. An online gateway denies access immediately after receiving revocation.

Host sleep or network loss marks sessions unavailable. Do not silently route work to a cloud host or a different model. Interrupted actions are not automatically retried when side effects are uncertain. Reconnection checks identity, grant revision, expiry, and budget again.

Default proposal: invitations expire after 24 hours; grants after 7 days unless shortened; session content is deleted 7 days after closure; minimal audit metadata is retained 30 days. Display and configure retention before activation. Deletion covers indexes, overlays, transcripts, artifacts, and scheduled cleanup of backups; offline hosts show pending deletion.

<a id="security-invariants"></a>

## 14. Security invariants and implementation constraints

These are release requirements, including for natural-language and tool-mediated requests:

- **SEC-01:** excluded source content is absent from recipient model context, retrieval stores, filesystem, and tool responses.
- **SEC-02:** every operation is bound to authenticated recipient, version, current grant, and host policy; identifiers are not authorization.
- **SEC-03:** runtime restrictions remain effective when the model follows malicious instructions.
- **SEC-04:** no direct mutation of the original project; recipient work stays in an isolated writable area.
- **SEC-05:** credentials, host control sockets, private agent sessions, and unrestricted MCP capabilities are not inherited.
- **SEC-06:** recipient isolation covers prompts, process trees, storage, indexes, caches, logs, and artifacts.
- **SEC-07:** review approval binds the actual bytes and configuration used to activate the share.
- **SEC-08:** revocation, expiry and exhausted budgets prevent new work and stop affected execution within a tested deadline.
- **SEC-09:** logs, metrics and crash reports never contain raw credentials, and content collection is explicit.
- **SEC-10:** unsupported isolation or enforcement capabilities block activation.

For P0 actions, accept typed parameters and construct an argument vector for a fixed executable inside the approved image. Do not interpolate user text into a shell command. Check executable identity and permitted input files; a command name alone is not a boundary.

Treat project code and dependencies as untrusted with respect to the host. Container-only development mode may use synthetic data, but must not be labeled safe for external collaborators. The private pilot requires a validated VM-backed runtime or an equivalently justified isolation design, including no host escape through mounts or tool adapters.

Enforce network destinations and operations outside the model. Protect against loopback, metadata endpoints, private network access, redirects, and DNS changes where networking is enabled. An allowed domain alone may still expose powerful operations; gateways must scope methods/resources as needed.

Generated files are untrusted. Render previews without active scripts, constrain file paths, and never execute exported patches or artifacts automatically. Inspection and download endpoints receive the same permission checks as chat tools.

<a id="deployment-and-models"></a>

## 15. Deployment and model routing

**Owner-local mode:** the host executor and storage run on the owner's Linux computer. A browser client can be on any supported desktop OS. The machine must remain reachable and available for active work.

**Development-server mode:** the executor runs on a server the owner is authorized to delegate. Enrollment requires an explicit server identity, constrained service account, configured runtime, and resource ceiling. An SSH connection may assist installation; it is not exposed as the collaborator's unrestricted tool.

**Gateway:** the pilot supports a manually configured authenticated HTTPS endpoint. It may sit beside the host or on a separate owner-controlled service. If a relay is used, document whether it terminates encryption and which data it can see. Do not assume end-to-end encryption merely because HTTPS is present. Host management endpoints are never exposed as public tool endpoints.

The setup wizard must test identity, encrypted connectivity, host authenticity, runtime capabilities, storage, and cancellation before sharing. Port forwarding or a tunnel is not an authorization system. Public exposure is an explicit deployment action.

A fresh session agent uses an approved model endpoint. Support a configured provider through an adapter; local inference is optional if a compatible endpoint is available. No cloud fallback is allowed without owner approval. The reviewer sees which operator/provider can process their content.

The initial agent importer supports explicit files and reviewed context. Existing agent integrations may later supply structured history, tool definitions, and provenance. They must never attach the recipient to a source agent's private memory, permissions, or hidden state.

<a id="reliability-and-cost"></a>

## 16. Reliability, resource use, and performance

The performance hypothesis is lower time from a handoff request to the first successful verification, especially when large approved assets and dependencies already exist on the host. Local execution is not universally faster than a warm cloud sandbox.

Measure preparation, review, environment readiness, model response, queueing, execution, and result delivery separately. Count owner time as well as machine time. Compare warm and cold starts fairly and include competing host workload.

Resource limits cover CPU, RAM, process count, writable disk, log/output size, run wall time, concurrent runs, network transfer where enabled, and model calls/tokens/spend. Configure finite defaults from host capacity. If a provider cannot expose a hard currency cap, reserve conservative token/call budgets and disclose possible billing lag.

Do not execute long work inside the web request. Use a job queue with cancellation, idempotency identifiers, and durable status. Duplicate submissions must not duplicate expensive jobs. Avoid automatic retries of uncertain side effects.

Proposed pilot targets, to validate before describing them as product performance:
- The clean demonstration project activates without manual environment repair after setup.
- Owner preparation is materially lower than the manual handoff baseline on the same tasks.
- Local control operations, excluding model and compute time, have p95 latency below one second on the reference deployment.
- Revocation and disconnected-host behavior meet the lease and cancellation targets in Section 13.
- A collaborator cannot exceed the configured runtime or aggregate budget through concurrent requests.

Accessibility requirements include keyboard operation, textual run status, clear error recovery, and no reliance on color alone. Long runs show progress, remaining budget where measurable, and a cancel control.

<a id="evaluation-and-release-gates"></a>

## 17. Evaluation and release gates

| Check | Required evidence before the private pilot |
| --- | --- |
| EVAL-01: end-to-end usefulness | A second authenticated user asks a sourced question and completes an approved rerun |
| EVAL-02: excluded content | Planted canary secrets in source-only files, context, history and environment never enter session inputs or outputs |
| EVAL-03: path boundaries | Traversal, symlinks, hard links, archive extraction and source changes cannot expand materialized access |
| EVAL-04: action boundaries | Prompt injection and malformed parameters cannot invoke unapproved executables or tools |
| EVAL-05: session separation | Two identities with different grants cannot access each other's files, searches, messages or artifacts |
| EVAL-06: source integrity | Hashes and filesystem checks show original files unchanged after a session |
| EVAL-07: revocation | Active streams, queued jobs, child processes, reconnects and downloads obey revoke and expiry |
| EVAL-08: resource limits | Concurrent jobs, output floods and repeated requests stay within configured limits |
| EVAL-09: network and credentials | Unauthorized services are unreachable; no credentials appear in model inputs, logs or exports |
| EVAL-10: update safety | A source change or new share version never becomes visible without the required approval |
| EVAL-11: failure recovery | Offline host, service restart and uncertain job status recover without replaying unauthorized work |
| EVAL-12: visitor privacy | Another recipient cannot see a session; owner visibility and retention match the disclosed policy |

A passing finite test set is evidence about tested cases, not proof of zero leakage. Before real external use, review the selected runtime's threat model and commission targeted security review as capacity permits. Any known boundary bypass blocks that release.

Run a small pilot with 5–10 owner/collaborator pairs and repeat tasks: an evidence question, a rerun, a parameter change, a request outside scope, and later a scoped edit. Use the same source project and task requirements across comparisons.

Baselines:
1. Manual selected files, a written explanation, and owner-operated reruns.
2. A manually prepared isolated environment with the same approved data and tools.
3. A remote collaboration workspace with comparable access settings.
4. Prism with manual scope selection, then Prism with assisted proposals.

Measure owner preparation time, approval count, collaborator completion, unsupported claims, unnecessary refusals, boundary violations, time to first successful verification, resource use, and repeat adoption. Separate errors in authorization selection, evidence sufficiency, enforcement, and answer quality. Publish de-identified fixtures and reproducible evaluation scripts when ready.

<a id="roadmap"></a>

## 18. Delivery roadmap and first implementation backlog

The [maintained visual roadmap and implementation status](roadmap.md) tracks observed delivery progress, evidence and owner acceptance against these milestones. This section defines the intended sequence; its deliverables are not claims that every capability already exists.

| Milestone | Deliverable | Exit condition |
| --- | --- | --- |
| M0: architecture spike | Runtime decision record, threat model, synthetic fixture, host prerequisite checks | Required boundaries can be enforced on the reference Linux host |
| M1: local vertical slice | Manual content selection, immutable version, fresh agent, evidence chat, one typed runnable | A local second-user test completes the reference review scenario |
| M2: private hosted pilot | Identity, HTTPS gateway, grants, budgets, leases, revocation, audit and retention | EVAL-01 through EVAL-12 pass on supported configurations |
| M3: limited continuation | Scoped workspace edits, approved tools, patch/result export, owner acceptance | Collaborator returns a useful change without source mutation |
| M4: easier preparation and open release | Assisted scope proposals, additional importers, documented setup, license, release checks | Pilot evidence supports usefulness; published support and security limits match behavior |
| M5: additional hosts and compute | Validated desktop host adapters, GPU scheduling, broader workflows | Platform-specific isolation and resource claims are tested |

Dependencies are intentional: complete isolation and authorization before inviting real external users; complete a manual sharing path before relying on model-generated policies. M1 is a synthetic local demonstration, not a secure public release.

**Delivery-mode ordering:** Keep M1/M2 focused on completing and validating online sharing before implementing independent handoff. The latter is a separate deferred workstream with its own owner-approved plan and acceptance, described [below](#delivery-mode-delivery-order). Existing milestone identifiers remain unchanged. Documenting a second mode does not authorize parallel implementation or imply that scoped continuation in M3 already includes environment export.

First backlog, in dependency order:
1. Record the reference host and choose a VM-backed runtime after a short capability spike.
2. Define the version manifest, grants, runnable schema, artifact records, and policy decisions.
3. Build materialization and immutable storage with hostile-path fixtures.
4. Implement a policy service and host executor that check the same policy revision.
5. Add a single approved evaluation action with bounded parameters, outputs, and cancellation.
6. Add an agent interface limited to approved evidence and action adapters.
7. Add identity, invitation redemption, owner review UI, and collaborator workspace.
8. Implement resource reservations, revocation leases, artifact access, and lifecycle cleanup.
9. Run the release gate matrix and the reference demo.
10. Invite the private pilot only after the release criteria pass.

The UI may first use explicit file selection and forms. Natural-language coaching is an enhancement, not a reason to postpone the functioning manual workflow. No fine-tuning is required to begin.

<a id="open-source-plan"></a>

## 19. Open-source project plan and repository organization

Build a useful self-hosted core: version preparation, grants, policy checks, host execution, collaborator interface, and reference fixtures. Do not require a proprietary control service for the core handoff path. Optional hosted relay, managed compute, or enterprise integrations can be evaluated later.

At this baseline, no license has been selected and the repository is private. Maintainers must select a license before describing a release as open source. This document does not change visibility, apply a license, publish a release, or commit to a commercial model.

Proposed future structure, not files claimed to exist:
- apps/: owner and collaborator interfaces.
- services/: control service and host executor.
- packages/: manifests, policy model, adapters and shared types.
- examples/: synthetic review and handoff projects.
- tests/: integration and adversarial fixtures.
- docs/: product design, setup, security model, architecture decisions and contribution guides.

Before public release, provide a supported-platform matrix, setup and teardown guide, explicit threat model, responsible vulnerability-reporting route, dependency/update policy, and reproducible demo. Pin release dependencies and record runtime compatibility. No private research material, credentials, real conversation history, or model-provider secrets belong in examples.

Keep all repository and Wiki documentation in English. Local research translations are maintained outside the repository. Follow the existing contribution rules: a focused branch per change, review before merging into main, and no private artifacts.

The first implementation conversation should read this specification, inspect the current repository, and start with M0. Select the application stack and runtime through explicit architecture decisions; this specification does not pretend code or infrastructure already exists.

<a id="competitive-positioning"></a>

## 20. Competitive positioning and evidence

The positioning is an owner-hosted, task-scoped review and handoff of existing personal agent work. Compete on preparation effort, usable follow-up capabilities, enforced recipient boundaries, and deployment ownership.

Existing products already cover substantial parts of this workflow. Do not claim to be the first shared agent, the first local sandbox, or the only private agent-sharing product.

| Reference | Documented overlap | Implication for Prism |
| --- | --- | --- |
| [UpServe external sharing](https://docs.upserve.app/en/agents/sharing) | Review-assisted selection, isolated agent copies, visitor workspaces and permitted execution | Direct competitor; privacy review plus a shared agent is insufficient differentiation |
| [Manus sandbox](https://manus.im/blog/manus-sandbox) and [Collab](https://help.manus.im/en/articles/12135428-how-can-i-use-manus-collab) | Existing task collaboration in a sandbox; connector/cookie controls; owner checks sensitive content | Compare the effort and exposure of preparing a scoped handoff |
| [Live Share](https://learn.microsoft.com/en-us/visualstudio/liveshare/reference/security) | Remote use of owner resources, file visibility controls and shared terminals | Local resource access alone is established |
| [Coder workspace sharing](https://coder.com/docs/user-guides/shared-workspaces) | Authenticated shared workspaces, roles and agent-to-human review scenarios | Workspace sharing and review are established adjacent capabilities |
| [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/security/) | Local isolation, controlled workspace access, network and credential boundaries | Reuse and validate suitable infrastructure |
| [Code Ocean peer review](https://docs.codeocean.com/osl-guide/publishing-on-code-ocean/peer-review) | Frozen private computational review copies | Reproducible review packages have direct precedents |
| [E2B snapshots](https://docs.e2b.dev/sandbox/snapshots) | Filesystem and memory snapshots with multiple derived sandboxes | Snapshot/fork is an infrastructure capability, not a standalone novelty claim |

These observations were checked against public primary-source documentation on 2026-09-16. They describe documented capabilities, not independent product or security tests. Missing documentation is not proof of missing functionality.

The [Related Works Wiki](https://github.com/Kishore712/prism/wiki/Related-Works) and [Innovation Assessment Wiki](https://github.com/Kishore712/prism/wiki/Innovation-Assessment) provide the earlier research background. This product specification records the current product direction; historical research hypotheses are not evidence that the market is empty.

<a id="example-contract"></a>

## 21. Illustrative sharing contract

The following is a conceptual contract for the reference scenario, not an implemented configuration format. Identifiers and limits are examples. The implementation must resolve actual content and environment digests and validate the host's ability to enforce every requested limit.

~~~yaml
share_version:
  id: experiment-review-v1
  purpose: Review the reported evaluation result
  content_manifest: approved-files.json
  environment_digest: "<resolved immutable image digest>"
  context_bundle: reviewed-context.json
  actions:
    - id: evaluate
      executable: /opt/project/evaluate
      input_schema:
        seed: {type: integer, minimum: 0, maximum: 1000000}
      input_dataset: approved-evaluation-data
      output_directory: /session/results
grant:
  recipient_subject: reviewer-001
  version_id: experiment-review-v1
  preset: Verify
  capabilities:
    - evidence.read
    - evaluate.run
    - artifacts.read
    - artifacts.download
  expires_at: "<owner-approved UTC timestamp>"
  limits:
    cpu_cores: 2
    memory_mib: 4096
    writable_disk_mib: 2048
    max_processes: 64
    max_run_seconds: 600
    total_run_seconds: 1800
    concurrent_runs: 1
    max_output_mib: 100
    max_model_calls: 100
    max_model_tokens_total: 100000
  network: deny
  source_writeback: deny
~~~

The model endpoint is configured separately by the trusted service; the session runtime's network denial is not permission to contact a model provider. Provider credentials remain outside the sandbox. Evidence reads, downloads, job submission, model calls, and output delivery all consume the same current grant.

For Continue, additional file-edit and execution rights require a new explicit grant. The preset must not silently introduce a general shell or access to files omitted from this version.

<a id="decisions-and-open-questions"></a>

## 22. Decisions, assumptions, and open questions

### Fixed product decisions

- Lead with privacy-preserving agent sharing and handoff; review is the first use case.
- Support owner-local and authorized development-server execution.
- Keep the snapshot owner-hosted by default and bind access to named recipients.
- Separate the private preparation agent from each collaborator's agent and workspace.
- Enforce access outside the model; share versions are immutable and grants are revocable.
- Preserve the original workspace; continuing work produces separate changes.
- Build a manually usable open-source core before adding learned privacy inference.
- Publish only English repository documentation.

### Initial design choices to validate in M0

| Choice | Initial proposal | Revisit when |
| --- | --- | --- |
| Host platform | Linux host; browser client independent of OS | Desktop-host demand and isolation adapters are validated |
| Isolation | VM-backed runtime; synthetic container-only developer mode | A capability spike establishes a justified alternative |
| Authentication | Configured OIDC provider and named invitations | Pilot deployment friction suggests another established method |
| Source import | Explicit project files and reviewed context | Real users show which agent format is most valuable |
| Model execution | Configured endpoint through an adapter | Privacy or performance needs favor local inference |
| GPU | After CPU review flow and scheduling validation | Pilot tasks cannot demonstrate value without GPU support |
| Language/framework | Not selected | M0 records maintainability and adapter fit |
| License | Not selected | Maintainers prepare a public release |

### Product questions to answer through pilots

How often does a reviewer need to run rather than inspect? What percentage of follow-up questions are served by the first grant? Which information do owners consistently exclude? How much of preparation is dependency repair versus privacy review? Do users prefer their laptop or a continuously available server? What output provenance is sufficient for everyday review? Which continuation tasks need free-form execution?

These questions do not block this document or the first synthetic prototype. They prevent premature commitments to universal compatibility, arbitrary runtime capture, and a specific research method.

<a id="research-path"></a>

## 23. Research opportunities after product validation

The product can generate research questions after its manual path works:

1. **Scope selection with few confirmations:** infer candidate resources and capabilities from the handoff purpose, then ask only questions that materially change a decision.
2. **Sufficient authorized evidence:** prepare context and runnable checks that answer likely follow-ups while minimizing unnecessary disclosure.
3. **Recipient-specific continuation:** preserve different resource and action boundaries throughout multi-turn work, updates, and additional approvals.
4. **Measured trade-offs:** compare task completion, privacy failures, owner effort, startup cost, and runtime cost under the same workloads.

A small fine-tuned model may later help classify content, propose grants, or prioritize questions. Establish a concrete bottleneck and compare against rules, manual selection, and general-model baselines before training. Keep enforcement independent.

Local hosting, snapshots, role labels, and a chat interface are not by themselves evidence of a strong paper contribution. A research claim requires a distinct problem or method, strong related-work comparisons, and reproducible improvement on meaningful tasks.

Record pilot data only with participant permission, minimize collection, and separate product telemetry from research datasets. Public benchmarks should begin with synthetic or explicitly approved materials.

<a id="agent-implementation-addendum"></a>

## Addendum: Agent implementation design (2026-09-17)

The [Agent Implementation Design](agent-design.md) supplements this specification with the collaborator agent's responsibilities, reviewed handoff context, proposed Python/PydanticAI integration, tool contracts, bounded execution loop, session memory, citation handling, and incremental acceptance criteria. It preserves the P0 boundaries and does not replace the sections above.

Its framework and implementation choices are proposals for review, not installed dependencies, approved implementation work, or validated capabilities. Development agents must follow the repository's [AGENTS.md](../AGENTS.md), including the plan-confirmation and per-feature acceptance gates.

<a id="delivery-modes"></a>

## Addendum: Two delivery modes and implementation priority (2026-09-17)

**Decision:** Design Prism for both **online sharing** and **independent handoff**, while implementing online sharing first. Independent handoff remains planned and unimplemented. This addition preserves P0, security invariants and milestone acceptance. It specifies a future environment export without claiming implementation readiness or research novelty.

### Two modes, one preparation foundation

| Dimension | Online sharing — first priority | Independent handoff — deferred |
| --- | --- | --- |
| Main user need | Quickly inspect, verify or perform approved work using resources the owner provides | Take over an approved project subset and continue without depending on the original host |
| Execution location | Owner's machine or a development server the owner is authorized to delegate | Collaborator's machine or a server the collaborator is authorized to operate |
| What is delivered | Authenticated access to a fresh agent session bound to a reviewed version | An approved, versioned package that reconstructs an environment and starts a fresh agent |
| Resource responsibility | Owner/host provides compute and storage; the configured model route has explicit credentials and budgets | Collaborator provides compute, storage, local permissions and model credentials/budgets |
| Availability | Active use depends on the owner-controlled host and required services remaining available | Accepted capabilities run without the original host; declared model or other external services can still be required |
| Writes and results | Separate recipient workspace; changes return through explicit owner review | Separate imported workspace under collaborator control; returning changes remains a reviewed action |
| Original owner's control | May restrict and revoke future access and stop affected work through the validated runtime lifecycle | Controls initial disclosure and future downloads/updates or owner-operated services, but cannot recall an acquired copy or prevent independent use |
| Typical duration | Short reviews and quick collaboration; longer hosted collaboration is also possible | Long-term project transfer and maintenance; short reproduction tasks are also possible |

Duration is a use case, not the mode boundary. The distinction is who controls the execution environment and whether operation depends on the original host. **Owner-local** and **Development-server** are both placements of online sharing, not these two delivery modes.

Delivery mode is separate from **Inspect**, **Verify** and **Continue** capabilities. P0 still supports Inspect and Verify in online sharing; scoped continuation remains later work. Independent handoff does not automatically require unrestricted editing, and online sharing can support scoped edits in a future approved stage.

Both modes use explicit selection, reviewed context, frozen versions, dependency identities and evidence/run provenance. Both create a fresh agent from approved material instead of attaching to the owner's unrestricted agent. A dependency image is one component: project files, curated agent context, active processes and process memory are distinct. Neither mode promises raw process or GPU-memory capture.

### Online sharing: reuse resources under a current grant

The owner selects a purpose, recipient, permitted material and actions; reviews the exact version and execution definition; and activates a separate agent session on an authorized host. The collaborator enters through a browser to question evidence, run permitted checks and request additional access. The owner reviews activity and can revoke subsequent access. This remains the primary journey in Sections 8–18.

Reusing local dependencies, immutable approved assets and available compute can reduce transfer and setup. It does not remove selection or review, mount the entire source workspace, or grant unrestricted host access. Actual preparation and startup benefits remain hypotheses to measure. Runtime-readable material is treated as potentially disclosed, even without a download button.

Current grants, resource reservations, file/tool/network restrictions and revocation are enforced outside the model. Recipient writes remain separate from the source. Revocation stops future use, not prior disclosure. M1 is a synthetic local demonstration of this mode, not a completed authenticated remote pilot; see its [acceptance record](m1-local-sharing.md).

### Independent handoff: deliver a reviewed package

The future export deliberately transfers an approved subset beyond the owner's host. A container or VM image alone is not a sufficient agent handoff.

| Package component | Required content and review |
| --- | --- |
| Version and inventory | Included files/assets, digests, origin version, provenance, intended task and declared omissions; no hidden source paths or private filenames |
| Project material | Approved code, data, results and redistributable dependencies required by the stated capabilities; no automatic workspace or repository-history dump |
| Agent context | Reviewed task state, findings, decisions, limitations, next steps and explicitly selected conversation excerpts; no hidden reasoning or full-session export by default |
| Reconstructable environment | Reviewed image and/or pinned build definition, runtime and architecture requirements, resource needs and declared dependency acquisition; no universal-portability claim |
| Tools and startup | Tool/runnable definitions, entry point, parameter schemas, model integration requirements and a setup procedure inspected before execution |
| Verification | A reference check, expected outcomes or tolerances, source environment identity and destination validation record |
| Configuration requirements | Required settings and credential names, supported capabilities and unresolved dependencies; credential values are supplied by the collaborator |

The intended workflow is:

1. **Review the export.** Select an approved version and choose any additional results or reviewed context to transfer. Check redistribution permission, inspect the exact package and destination, and approve its digest and disclosure. Existing browser access or result-download permission is not permission to export an entire environment.
2. **Transfer without activation.** Use an explicitly selected transfer route, without a public link or exposed host by default. The recipient reviews the inventory and verifies digests against the approved artifact through a trusted transfer context. A digest alone does not establish sender identity or code safety.
3. **Check the destination.** Validate architecture/OS, runtime isolation, compute/storage capacity, dependencies and credentials. Missing or incompatible requirements block the affected capability. Do not silently substitute environments or contact the source owner's private services.
4. **Reconstruct and start.** After recipient review, create an isolated environment and fresh agent in a separate workspace. The destination operator supplies model configuration, credentials, finite budgets and permissions. Imported instructions do not authorize executing scripts, enabling tools or accessing the host.
5. **Accept the handoff.** Execute the reference check, open its evidence, record the destination environment and compare outcomes. Confirm that accepted capabilities work with the original host unavailable, while identifying approved external dependencies. Report incomplete handoffs rather than silently treating missing assets as transferred.

Never package the original owner's API keys, cookies, SSH material, live session tokens, grants or host leases. Review images and artifacts for embedded secrets and unintended content, including image layers and metadata. Selecting top-level files alone is insufficient. An asset that may be used on the owner's host but cannot be redistributed blocks the affected independent capability. The owner must approve a distributable substitute, explicitly narrow the deliverable, or keep that capability online.

The collaborator controls the destination host and can inspect or modify its copy. Original-owner policies embedded in the package are not a tamper-resistant boundary against that administrator. Destination-side enforcement protects the destination's resources under its operator's authority. The original owner can stop later downloads, updates or still-hosted services, but cannot revoke an acquired copy or prevent offline use. Local deletion is a recipient-side action, separate from revoking the online share.

Independent task execution does not imply offline model inference. An external model remains a recipient of prompts, approved evidence and outputs, requiring explicit destination-side configuration and disclosure. If a capability still depends on an owner-operated service, show that dependency and its revocability instead of labelling the capability independent.

### Transition from online review to independent handoff

A common progression is: online review → select materials and review outcomes worth transferring → review a new export candidate → transfer → validate at the destination. Export is a separate owner action. It neither automatically includes all reviewer conversations nor retires the online share. Revoking that share later does not revoke the package. Returned changes have separate provenance and require review before updating the original project.

<a id="delivery-mode-delivery-order"></a>

### Delivery order and acceptance

**First complete online sharing:** owner preparation → independent collaborator session → evidence conversation → actual approved verification → owner activity/access review → revocation. M1 owner review and subsequent authorized M2 private-pilot work retain their existing gates, including authenticated recipients, validated isolation, budgets, leases and revocation. Do not postpone these requirements to build the second mode.

**Then schedule independent handoff separately.** Implementation starts after online sharing meets its relevant private-pilot gates and is accepted by the owner, and the owner approves a concrete handoff stage. The relative order of this deferred workstream and other later milestones will be decided then; M0–M5 are not renumbered here. This documentation decision does not authorize M2 implementation, exporting resources, additional spending or independent-handoff development now.

| Deferred increment | Observable acceptance | Failure and boundary acceptance |
| --- | --- | --- |
| Reviewed export candidate | Owner inspects the package inventory, context, dependencies, omissions and disclosure notice, then approves an exact artifact | Excluded canaries/credentials/history are absent; changed bytes invalidate approval; online access does not authorize export |
| Safe import and reconstruction | Collaborator inspects and reconstructs the package on one explicitly supported destination configuration | Tampering, unsafe paths, unsupported runtime/architecture and missing dependencies block affected activation; import does not automatically execute host commands or broaden permissions |
| Fresh destination agent | Collaborator supplies local configuration, asks an unscripted evidence question, follows a citation and completes actual reference verification | No inherited owner session, key, grant or private-source access; imported instructions cannot authorize new tools, networking or mounts |
| Independent acceptance and lifecycle | Accepted workflow succeeds with the original host unavailable and declared external services accounted for | UI explains that online revocation cannot recall copied content; updates, returned changes and local deletion are separate explicit actions |

Use synthetic or explicitly approved fixtures and report actual supported environments. Measure export preparation effort, transfer size, reconstruction time, missing dependencies and useful follow-up completion. These results do not establish absolute safety, universal portability or research novelty. Export format, supported destination runtime and distribution mechanism remain decisions for that later stage.

<a id="owner-project-workspace"></a>

## Addendum: Owner project workspace and conversation handoff (2026-09-17)

**Subsequent M2.1c checkpoint:** [Approved-context continuation](m2-context-continuation.md) is implemented and awaiting owner review. Fresh collaborators receive reviewed background as untrusted data, selected evidence and existing scoped tools. Background, historical results and new runs have separate validated references. No owner-private history, privileged messages, arbitrary tools or live state is transferred. Fifty-four focused tests and a live failed-answer/successful-recovery sequence are recorded; private-pilot readiness remains false. This supersedes the older blocked-activation status below.

**Latest M2.1b checkpoint:** Following explicit scope approval, [manual reviewed-handoff preparation](m2-reviewed-handoff.md) is implemented and awaiting acceptance. Owners can select completed work, edit and review disclosure, and approve an immutable contextual version. The legacy recipient entry point cannot activate these versions; contextual collaborator continuation remains M2.1c. Automatic boundary proposals, arbitrary project import and pilot readiness are not included. Earlier implementation notes below remain dated checkpoints.

**Subsequent implementation checkpoint:** The user later authorized M2.1a only. Private owner project chat, evidence and fixed-action runs are implemented for the synthetic project and awaiting acceptance; see [the delivery record](m2-owner-workspace.md#current-delivery-record). The direction and broader requirements below are preserved. Reviewed context selection and independent contextual continuation remain planned; no entire-M2 authorization or pilot readiness is implied.

**Confirmed product direction; implementation planned.** The owner should have a project agent and working conversation in Prism. Sharing starts from that work. The earlier owner dashboard and preparation journey describe administration after work has already happened; they are not the complete intended owner experience. M1 implements that limited path, and its Owner **Conversations** page only inspects collaborator sessions. The [M2.1 proposal](m2-owner-workspace.md) is the first planned M2 workstream for filling this gap.

### Work, review, continue

1. **Work in the owner project.** Open the project Chat, discuss explicitly configured project resources, perform permitted tasks, inspect actual results and retain private project conversation history. The owner agent may have broader project access than a collaborator; it remains subject to project configuration, host policy and finite budgets.
2. **Choose a handoff checkpoint.** Select a completed point in the conversation, relevant visible message excerpts, task state, decisions, limitations, open questions, files and result records. Select permitted future actions separately. An action performed in the past is not authorization to perform it again.
3. **Review exact disclosure.** Preview the actual text and bytes the collaborator will receive. Include, omit or explicitly edit excerpts and summaries; edited content is labelled accordingly. Freeze and approve a version covering both resources and context. A private fact quoted in a selected answer is still disclosed even if its source file is excluded.
4. **Continue with approved background.** The collaborator enters a separate session showing the reviewed prior work, approved sources and available actions, then asks a follow-up or requests a permitted verification. Present prior exchanges as imported background, not as the collaborator's own messages or freshly executed tool results.
5. **Keep branches separate.** New owner work stays private unless selected into another reviewed version. Collaborator turns and outputs stay in that session; source changes require a separate owner review. Revocation controls future access to the shared branch and cannot recall information already delivered.

### Surfaces and domain extensions

| Addition | Intended role | Disclosure and authority |
| --- | --- | --- |
| Owner project workspace | Chat as the main work surface, with resources and runs nearby; sharing and collaborator activity as separate destinations | A project-scoped owner assistant, not unrestricted host access or a copy of the reviewer UI with a different label |
| Owner working conversation | Private questions, agent responses, evidence and actual run references | Separate owner authorization and storage scope; never a collaborator endpoint |
| Reviewed handoff context | Frozen summary, ordered excerpts, task state and selected result evidence | Included in the approved version digest and shown exactly in the disclosure preview |
| Collaborator continuation | Approved background plus the recipient's own subsequent history and workspace | Fresh session authority, current grants and isolated execution state; no live attachment to the original owner agent |

This supplements the core domain model: the original **Session** object remains a collaborator session, while an **Owner working conversation** has its own authorization scope. The same agent framework may support both, but their context builders, retrieval scopes, mutable state and tool authority must not be shared implicitly. The source mapping for a reviewed excerpt stays owner-side; recipient references resolve within the approved version.

**Fresh session does not mean empty context.** It means fresh recipient-bound state and authority. Reviewed background can preserve task continuity without copying unselected history, hidden reasoning, credentials, system instructions, provider handles or active processes. Quoted instructions cannot change policy and historical tool calls cannot run on import.

### Delivery and acceptance

Implement owner work, reviewed checkpoint selection and collaborator continuation as three small capabilities within M2.1. Start with the existing synthetic workload and fixed action, then continue the broader M2 importer, Linux runtime, remote identity and lifecycle backlog. A native owner working agent is distinct from the future automated sharing-preparation assistant; explicit manual selection remains the default. External source-agent adapters, general coding tools, arbitrary shell and automatic writeback are not introduced by this clarification.

Acceptance requires a real owner conversation and run, exact review of selected context/resources, a contextual collaborator follow-up and an actual permitted rerun. Check normal paths and direct attempts to read private owner history, cross-project data or other sessions; inspect assembled model inputs, not just answers. Preserve existing model reservations and disclose owner-side provider data categories before enabling the new route. See [the implementation order and acceptance scenario](m2-owner-workspace.md#agile-implementation-order).

The owner's confirmation approves this direction and documentation work. Implementation scope still requires its next authorization. It does not accept M1, enable private-pilot use, approve new spending or change the online-sharing-first priority. No novelty, usefulness or stronger safety result is established by adding this journey.


## General-purpose handoff interface (2026-09-19)

The owner clarified that Prism is a general agent handoff product. Experimental reproduction is one validation scenario, not its primary information architecture. The authorized interface refinement makes conversation the task entry point, with shared background/resources, capabilities/permissions, and activity/results as supporting views. It removes dedicated experiment launch forms and top-level statistical dashboards.

Specific action parameters, output and execution provenance belong in the corresponding task details. Exact owner approval must still disclose the executable and its parameter/resource limits; generic presentation must not obscure what is being authorized. Imported historical results remain separate from new session activity. Failed answers and completed executions must remain distinguishable so users do not unknowingly repeat work.

This interface change does not implement arbitrary tools, project import or unrestricted execution. The existing synthetic action remains the only executable capability. Agent tool calls and direct APIs retain their server-side checks, and additional access requests grant nothing until a later approved workflow implements expansion. The product can adopt further bounded tools without adding a separate application-level screen for each tool.

See [the implementation and acceptance guide](general-handoff-workspace.md) for this increment; earlier experiment-specific walkthroughs describe historical interface checkpoints.


<a id="m2-project-import"></a>

## Addendum: M2.2 explicit project import and bounded JSON action (2026-09-19)

The owner separately authorized M2.2 on `prototype/m2-project-import` after the general-purpose workspace refinement. This increment is deliberately narrow: configure one or more local `.prism-project.json` sources through repeatable operator `--project` options, choose one configured source, select explicit files, and approve a frozen copy. A direct file-only handoff may use that selection without a model call. A private owner chat may use its own explicitly selected context only after the existing model-consent disclosure. Private-session selections and New Share selections are separate; New Share starts with imported project files unselected. A `README.md`-only selection remains valid for owner chat inspection.

The supported manifest is strict and versioned: `schema: 1`, a slug `id`, a string `title` of at most 120 characters, one to eight explicit relative UTF-8 filenames, and `action: "json-check"` or `null`. Each filename is at most 240 characters and eight path segments. Its configuration is capped at 16 KiB; individual files at 32 KiB; and the sum of selected file bytes in an approved immutable version at 96 KiB. The application records `source_kind` as `local-project` for configured local sources, including the `document-handoff` acceptance example, and `synthetic` only for the legacy paired-evaluation fixture; it is not an extra manifest field. The importer rejects path escapes, links, hard links, special files and unsupported targets. It does not recursively scan, disclose unselected paths or files to collaborator/model context, or permit a parent/nested manifest to override the selected source.

`json-check` is the only new action. Before creating an executable version, Prism preflights its base64 action payload; the encoded payload is limited to 100 KiB. It parses only approved selected JSON inputs inside the existing pinned development runtime, with network disabled, no host mount, and the M2.2 bounds of 128 MiB memory, 0.5 CPU, 32 processes, 10 seconds, 8 MiB scratch and 64 KiB captured output. JSON depth is limited to 80 and integer values to 256 digits. The action does not run project scripts, install packages, access the project root, or use arbitrary tools. A failed check uses `valid: false` and distinguishes `error.reason` values `syntax`, `unsupported_depth`, and `unsupported_number`.

The project picker and direct handoff path are local-development behavior. Acceptance uses the newly authorized English synthetic `document-handoff` example; configuring an operator's other local files is not evidence about private workspaces. The owner may explicitly select a private note for the private owner session and send that selected context to the owner model after consent; that choice does not add the note to New Share. Collaborators receive only their reviewed selection. Source changes cannot modify an earlier approved version. The unified activity/results surface records a new bounded action result separately from historical owner results. Model output remains untrusted content and cannot grant access, add files, select tools, or change runtime policy.

**M2.2 validation checkpoint (2026-09-20):** The increment is implemented and validated in local development and awaits owner review. The [validation record](validation/m2-project-import.json) records the backend, bounded runtime, frontend, browser, preservation, and zero-model-call evidence. The audit restored unrelated formatter changes before confirming compatibility of existing hash-bearing bootstrap actions and runs. No live model JSON response, reference-Linux integration, or private-pilot readiness is claimed. After M2.2 review, M2.3 addresses reference-Linux runtime integration under a separately reviewed scope.
