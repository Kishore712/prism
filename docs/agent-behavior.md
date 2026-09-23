# Agent Behavior: Refinement and Research Plan

- **Date:** 2026-09-17.
- **Status:** Proposed refinement plan following owner feedback. No new behavior implementation or experiment result is claimed by this document.
- **Scope:** Improve the online-sharing collaborator agent within the existing P0 authority boundary. Preserve milestone acceptance, model budgets and deployment approvals.
- **Related documents:** [Agent architecture](agent-design.md), [delivery roadmap](roadmap.md), [M1 evidence and limitations](m1-local-sharing.md), and [research validation](product-research-validation.md).

## 1. Why the agent needs a dedicated workstream

M1 demonstrates that a fresh agent can answer an evidence question and invoke a real approved verification. It does not establish that the agent reliably chooses useful actions, recognizes insufficient evidence, recovers from failures or reduces owner effort.

The [live record](validation/m1-live-model.json) contains five turns: three completed and two failed. One failed answer followed a completed verification. An intermediate answer conflated a session rerun with the original baseline; a later concise follow-up separated them after prompt refinement. The earlier generic errors do not establish their precise causes. These are concrete regression candidates, not a statistically meaningful success rate.

The current implementation in [conversation.py](../src/prism/conversation.py) combines a bounded model/tool loop, behavioral instructions, structured answers and service-side checks. Its instructions already address evidence reading, execution intent and provenance. Much of that desired behavior has not been independently measured. Reference validity and tool authorization cannot establish whether an answer is supported or an action was useful.

Agent development therefore remains an ongoing product workstream across M1, M2 and later milestones. Connecting an SDK or completing one successful conversation is not completion of the agent design.

## 2. Observable behavior contract

Judge visible actions, evidence use and outcomes, not hidden model reasoning. These are proposed acceptance cases to implement and evaluate, not assertions that the current agent passes them.

| Dimension | Desired behavior | Concrete acceptance case |
| --- | --- | --- |
| Understand the request | Distinguish explanation, evidence inspection, requested execution and unavailable capability. Clarify only when ambiguity affects the result or action. | An evidence-only question reads evidence without launching a run. An explicit permitted rerun proceeds without unnecessary owner approval. An ambiguous action request gets one relevant clarification. |
| Ground claims | Read the supporting evidence; distinguish reported findings, new completed runs and interpretation. Preserve conflicts and limitations. | A claim about `baseline.json` cites that file. A seed-23 result cites the actual completed run. Contradictory or absent evidence is acknowledged rather than filled in from plausibility. |
| Select useful actions | Use the evidence and existing same-session results needed for the task; avoid redundant search, polling and execution. | A question about a completed run inspects its record rather than resubmitting it. A genuinely new allowed seed produces the requested new run. |
| Diagnose gaps | Distinguish evidence insufficiency, missing dependency, permission denial, unavailable host, execution failure and model failure from observable service information. | A missing answer in the approved catalog is not described as a known private file. An uncertain failure is labelled uncertain rather than assigned an invented cause. |
| Recover and stop | Preserve completed work; bound retries and stop with an actionable partial result when necessary. | If generation fails after a job completes, a later authorized status/recovery path can find that job without automatically rerunning it. Budget exhaustion never silently raises the allowance. |
| Ask for bounded help | Explain the specific task obstacle and the smallest justified addition or clarification; reuse valid existing grants. | A request describes needed evidence or capability and purpose without guessing hidden inventory. Pending/denied requests do not change access or trigger work. |
| Maintain continuity | Keep the current task, evidence, open questions and actual run state straight within the permitted session. | A follow-up retains source/run distinctions. A new recipient/session has no inherited history. Version or grant changes follow service policy, not a model-authored memory. |

Proactive suggestions may be helpful, but suggesting a new experiment does not itself authorize execution. Avoid both unrequested work and refusing useful work already within a valid grant and the user's request.

## 3. Implementation approach to test

Keep the initial agent single-agent and small. Introduce an additional planner, critic, memory system or model only if measured failures justify its complexity and cost. Fine-tuning is not a prerequisite.

Separate three concerns in ordinary, testable code rather than creating a general agent framework:

1. **Behavior decisions:** a bounded decision about answering, retrieving, checking an existing run, submitting requested verification, asking for clarification, proposing access, or stopping. A model decision is a candidate action, never permission.
2. **Observable task state:** the current user objective, inspected evidence references, reported/new-run distinction, actual run identifiers and status, unresolved gaps and service-supplied limits. Store only permitted session data; no owner-private memory or hidden chain of thought. A concise user-facing explanation is different from hidden reasoning.
3. **Trusted execution and validation:** existing services independently authorize each operation, reserve budgets, track idempotent jobs and validate references and delivery. Proposed plans, summaries, confidence scores and model-produced state cannot modify these controls.

Start by making the existing behavior reproducible and its failures visible. Compare focused prompt/tool-description changes with explicit task/evidence/run state only where the baseline fails. Keep policy checks identical between variants. Do not broaden tools, retrieve private material or disable limits to improve a behavior score.

An evidence ledger may associate claims with inspected fragments and completed run records, but it cannot mechanically prove semantic entailment. Deterministic checks should establish identity, scope, status and numeric facts where available; a documented human rubric should assess whether the explanation is supported and useful.

### Reuse an established agent foundation

The owner explicitly supports building on an established agent architecture with a substantial ecosystem. Prefer reuse for model/tool loops, typed interfaces, orchestration and supported persistence. Prism should concentrate its custom work on the handoff behavior, evidence/run semantics and independently enforced sharing policy. Adoption is a selection signal, not proof of fit or security; no comparative active-user counts have been established here.

The following assessment uses official documentation checked on 2026-09-17. It is a design recommendation, not a completed compatibility benchmark or an instruction to upgrade the current lockfile.

| Candidate | Documented reusable layer | Recommended role in Prism |
| --- | --- | --- |
| [PydanticAI](https://pydantic.dev/docs/ai/core-concepts/agent/) | Agent execution with registered tools, dependencies, message history and structured output | **Default for B0/B1:** retain the foundation already integrated and pinned in M1. Improve and compare behavior above it before considering migration. |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | Explicit stateful orchestration combining deterministic and model-driven steps, with persistence and human interaction | A candidate if the approved M2 workflow needs orchestration that would otherwise require substantial custom infrastructure. Evaluate a narrow workflow before proposing a migration; it is not an extra framework to add by default. |
| [OpenHands Software Agent SDK](https://docs.openhands.dev/sdk) | An extensible software-engineering agent with configurable tools and behavior | A candidate for M3 coding/continuation and a future coding-task comparator. Its broader execution tools do not match the current fixed-action P0 scope without deliberate restriction. |

PydanticAI also documents [durable execution integrations](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/); first assess whether the existing stack can satisfy the measured requirement. LangGraph [checkpoints](https://docs.langchain.com/oss/python/langgraph/persistence) record graph state. Neither a framework checkpoint nor its resume mechanism is a Prism authorization grant, a snapshot of running task memory, or proof that an external side effect is safe to repeat. Reauthorize at the tool/service boundary and reconcile real jobs under the existing lifecycle requirements.

The division of work is:

- **Reuse:** model adapters, the basic model/tool loop, typed schemas, and suitable framework orchestration/testing facilities.
- **Develop and evaluate in Prism:** scoped evidence assembly, source/run provenance, gap diagnosis, action/recovery policy, help-request quality and the controlled comparisons in this plan.
- **Keep outside model authority:** identity, grants, version binding, budgets, filesystem/network restrictions, job lifecycle and revocation. Existing isolation components may enforce these; framework prompts or approval widgets alone do not.

Adopt additional framework features only after checking the pinned version, explicit tool registration, automatic retry/replay behavior, model routing, state retention and tracing defaults. No generic shell, private workspace mount, shared cross-recipient memory or remote trace export becomes authorized through reuse. Do not fork a framework or build an abstraction spanning several engines until a demonstrated need outweighs the maintenance cost.

For research, keep the reused foundation and its competent default/tuned configuration visible in the baseline. Attribute changes to the specific behavior mechanism and test them under comparable tools, information and budgets. A framework migration or a renamed existing architecture is not itself a method contribution.

## 4. Proposed delivery increments

These work-item IDs do not replace M0-M5 or the existing A1-A3 capability design. They are proposed refinements for review, not automatic authorization for new milestones or spending.

| Increment | User-visible deliverable | Placement and acceptance |
| --- | --- | --- |
| B0: Behavior baseline | A repeatable local report showing which review tasks complete, fail, need help or cause unnecessary actions, with evidence for each outcome | Proposed first M1 refinement. Version cases, expected facts/allowed actions and scoring before changing behavior. Separate deterministic/double tests from real-model results; retain all failures and incomplete runs. |
| B1: Reliable evidence review | A reviewer can ask about the original result, inspect a new run, identify uncertainty and recover an already completed result without confusion or duplicate execution | M1 refinement after B0. Demonstrate the behavior-contract cases for provenance, evidence-only requests, missing evidence and completed-job recovery. Compare with the frozen baseline under the same limits. |
| B2: Useful bounded collaboration | A named reviewer receives precise gap explanations, useful clarification/access requests and predictable stopping/recovery across longer interactions | M2, after the corresponding identity, grant, host and lifecycle features exist. Evaluate appropriate help requests and permitted completion alongside direct authorization and revocation checks. |
| B3: Generalization and method experiments | A reproducible comparison on unseen projects and follow-ups identifies which behavior changes improve useful completion and owner effort | Prepare during the accepted online pilot and scope separately. Hold the runtime/policy boundary constant, use held-out tasks, compare strong baselines and report cost plus failures. Method development requires evidence of a recurring unsolved problem. |

B0 and B1 are the recommended refinement before treating the local agent as behaviorally mature. The existing M1 demonstration remains available for owner review. Agree the added acceptance scope before implementation; this proposal neither accepts M1 nor starts M2. B2 cannot use nonexistent M2 lifecycle controls as test evidence. Independent handoff stays deferred.

## 5. Evaluation that can support later research

### Start with a small regression suite

The initial cases should cover: evidence-only questions; a new permitted seed; baseline versus session-run provenance; absent/conflicting evidence; questions about completed work; generation failure after completion; insufficient budget; pending/denied access; injected document instructions; and separate sessions. Use synthetic or explicitly approved data.

The current fixed M1 fixture is suitable for regressions, not broad generalization claims. Failure injection and model/HTTP doubles validate control flow; they do not establish how a real model behaves. Do not report a test as a live failure observation when its failure was injected. Mark cases requiring unavailable project formats, lifecycle capabilities or model allowance as unrun.

For each case, specify the user's task, approved evidence/actions, expected facts, acceptable clarification or stopping conditions, forbidden effects and scoring rules before running a new variant. A refusal is correct only when the task cannot be fulfilled within the available scope; refusing all cases is not success.

### Record interpretable results

Record task/case version, code/prompt/tool-schema version, model configuration, approved snapshot identity, observed tool calls and outcomes, actual run references, final answer or safe failure, elapsed time and available usage. Keep full permitted transcripts only when explicitly approved for evaluation; default exports should contain synthetic data or minimal outcome records. Never collect hidden reasoning, credentials or unrelated content. Distinguish reservation amounts, token usage and actual billed cost; unknown values remain unknown.

Report separate outcomes:

- Correct, supported task completion and valid but unsupported claims.
- Unnecessary executions, redundant tool use and failure to reuse completed work.
- Appropriate clarification, unnecessary refusal and owner interventions/time.
- Gap classification accuracy and recovery after partial completion.
- Attempted unauthorized operations and independently verified blocked/successful effects. A blocked attempt is not a successful violation, but remains a behavior failure to analyze.
- Model requests/tokens, runtime use, latency and cost at the same approved limits.

Freeze rubrics and numeric acceptance targets before a comparative run. Repeat real-model cases within an explicit finite budget and report sample counts and variation; five demonstration turns cannot establish reliability. Do not silently change a model, increase limits, drop failed cases or score a variant on easier tasks. Optional model-based judging is supplementary and must be checked against known facts and human review; it is not an authorization test or ground truth by default.

### Compare mechanisms rather than demonstrations

For collaborator behavior, start with the current bounded agent as the baseline, then compare a competently tuned prompt using the same tools and a proposed explicit task/evidence/run-state variant. Keep model, approved information, tool capabilities, runtime, grants and budget constant; record unavoidable differences. Test the contribution of state/provenance tracking, recovery and clarification separately. A later stronger-model comparison is a separate experiment, not evidence for the behavior mechanism by itself.

Once multiple project formats and workloads exist, separate development tasks from held-out projects and follow-up families. Evaluation questions must not be available during share preparation or tuning. Merely paraphrasing known questions is insufficient. Keep deterministic enforcement tests alongside live usefulness tests; neither replaces the other.

## 6. Research direction and the two agent roles

**Owner-workflow clarification (2026-09-17):** The owner has confirmed a native **owner project-working agent**, followed by explicit review and contextual handoff; see [M2.1](m2-owner-workspace.md). This is distinct from the automated preparation assistant described below. The heading and original research-role discussion refer to collaborator behavior versus share-preparation methods, not a requirement that the owner have no working chat. Owner work, handoff preparation and collaborator continuation should be measured separately. This clarification adds no experiment result or implementation approval.

The immediate subject is the **collaborator agent**, operating on an already approved share: how should it gather evidence, choose permitted work, detect insufficient resources and request narrowly targeted help for unforeseen follow-ups?

A possible later **owner-side preparation assistant** addresses a different decision: which context, dependencies and actions should be proposed for review, and which clarification would make a future handoff sufficient? It is deferred assisted preparation, not an implemented second agent or permission to let the recipient agent search private owner material. Explicit manual selection remains the current product path.

For collaborator-policy experiments, freeze the reviewed package and runtime. For preparation-method experiments, hold the collaborator execution agent constant, as required by the [research validation plan](product-research-validation.md#6-experiments-to-build-into-development). Studying both together requires a separately specified experiment that can distinguish their effects.

**Candidate research question:** Can a behavior policy linking claims, permitted evidence, actual verification state and unresolved gaps complete unseen follow-up tasks with less unnecessary execution and owner intervention, at unchanged enforced authorization boundaries?

This is a hypothesis, not a novelty claim. Prompt polishing, tool calling, state tracking or adding multiple agents is not by itself a research contribution. First establish recurring failures, review relevant prior work for the concrete mechanism, and test a credible solution against strong alternatives. Useful product improvements remain worthwhile even if they do not support a method paper; measurement or interaction findings may suggest a different research path.
