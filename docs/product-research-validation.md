# Prism v0.1: Product Differentiation and Research Revalidation

- **Date:** 2026-09-17
- **Object of review:** [Product Design v0.1](product-design.md), including its actual P0 limits.
- **Status:** Evidence-based assessment and proposed experiments; no implementation or measured advantage is claimed.
- **Evidence:** Selected primary product documentation, repositories, and papers checked on the date above. This is a documentation review, not a hands-on product comparison or a security audit. An undocumented feature cannot be assumed absent. Preprints and protocol proposals are relevant prior art even when they are not mature products.

## 1. Decision

**Proceed with a focused open-source prototype. Treat product differentiation as a hypothesis to validate and research novelty as an unresolved question.**

Prism has a concrete target workflow: turn existing private agent work into a recipient-specific, executable handoff on an owner-controlled host. A supervisor or limited collaborator should be able to ask follow-up questions, inspect evidence, and rerun approved work with less help from the owner.

Individual ingredients already have substantial precedents. The remaining opportunity is whether Prism makes this complete workflow materially easier and safer to configure in practice. Public documentation does not establish an empty market, and this review does not establish product superiority.

The current specification is a credible product plan and potential research platform. It is not yet a demonstrated research contribution. In particular, a local sandbox, automatic permission proposal, or a benchmark of authorization failures alone would be weak novelty claims.

## 2. Product overlap and the remaining hypothesis

| Closest reference | Documented coverage | What Prism still needs to demonstrate |
| --- | --- | --- |
| [AgentSpace](https://github.com/HKUDS/AgentSpace) | Self-hosting, agent sharing, remote execution, permissions, approvals, and audit. Its roadmap lists a multi-agent isolation and sandbox policy layer as planned. | A usable path from an existing private project to an independently bounded recipient session, with measured preparation effort and verified isolation. |
| [Canopy](https://canopyagents.com/) | Browser access to local agent threads, observer/collaborator roles, and secret scrubbing. Shared transcripts are stored by its service. | A reviewed, versioned resource boundary and restricted execution environment for each recipient; a live conversation share alone is insufficient. |
| [UpServe external sharing](https://docs.upserve.app/en/agents/sharing) | Assisted sharing review, isolated public agent versions, visitor workspaces, and controlled access to execution resources. | A compelling owner-hosted workflow for existing research/development projects, including local assets and useful follow-up execution. |
| [Coder workspace sharing](https://coder.com/docs/user-guides/shared-workspaces) | Authenticated workspace sharing and roles, including agent-prepared work that a person reviews. | Lower effort to prepare a narrowly scoped share of private work, beyond granting access to a shared workspace. |
| [Manus sandbox](https://manus.im/blog/manus-sandbox) | Task execution and collaboration in an isolated sandbox. | Useful recipient-specific boundaries when the original project contains material that cannot be shared. |
| [Code Ocean peer review](https://docs.codeocean.com/osl-guide/publishing-on-code-ocean/peer-review) | Frozen private computational review copies. | Conversational follow-up and bounded continuation prepared with less owner effort on an owner-controlled host. |

These differences are **evaluation targets**, not assertions that competitors cannot provide them. Product integration can be valuable without constituting a new algorithm. The relevant comparison is the total work needed to accomplish the same handoff, not a count of features.

**Candidate product positioning:** “Prepare a private project for a named collaborator to question, verify, and continue within explicit information and execution limits, using resources you control.”

## 3. Research overlap is substantial

| Work | Relevant prior contribution | Consequence for Prism |
| --- | --- | --- |
| [AuthBench: Do Coding Agents Understand Least-Privilege Authorization?](https://arxiv.org/abs/2605.14859) | Infers file read/write/execute policies from a task and terminal environment; evaluates executable sufficiency and exposure. Its proposed decomposition separates coverage discovery from permission tightening. | “Automatically find sufficient but minimal permissions” is already studied. Its policy-generation process is agentic and multi-turn; do not describe it as merely one-shot classification. |
| [Progent](https://arxiv.org/abs/2504.11703) | Programmable tool policies, generated policies, and controlled policy updates. | Tool enforcement and asking for approval when permissions expand are established directions. |
| [Minimum Necessary Context](https://arxiv.org/abs/2608.01719) | Task-sufficient disclosure with recipient/purpose and contextual restrictions. | Minimal disclosure and purpose-bound sharing are not new problem statements by themselves. |
| [PPC: Share No More Than the Request Requires](https://arxiv.org/abs/2607.22953) | Holder-local evidence and authorized views for a recipient and purpose. It is a position/protocol proposal rather than an experimentally validated product. | Recipient-aware evidence preparation already has conceptual precedent. Lack of implementation does not erase that precedent. |
| [WeClawArena](https://arxiv.org/abs/2608.03499) | Cross-user agent collaboration in controlled workspaces with permissions and resource policies; includes software review workflows. | A multi-user sandbox benchmark or a reviewer persona is insufficient differentiation. |
| [MasDrift](https://arxiv.org/abs/2608.07556) | Measures authorization preservation across delegation architectures and compares defenses tied to authorization evidence. | Authorization loss during handoff is already an explicit benchmark target. |
| [GATE: Eliciting Human Preferences with Language Models](https://arxiv.org/abs/2310.11589) and [Automating Data Access Permissions](https://arxiv.org/abs/2511.17959) | Preference elicitation and prediction of user-specific permission decisions. | Reducing questions or learning individual permissions requires stronger comparisons than a generic agent prompt. |
| [ReproZip](https://docs.reprozip.org/en/latest/) | Captures execution dependencies for reproducible experiments. | Dependency discovery and packaging should be reused or compared against, not presented as new in themselves. |

Collectively, this literature already covers many pieces of the intended system. A new contribution must identify where a strong combination of these pieces still fails and explain why the proposed solution improves it.

## 4. Most promising research question

**Can a system prepare a sufficient, recipient-specific executable handoff from existing private work, with bounded owner effort, for follow-up requests that were not known when the share was prepared?**

The unit of study should be a real handoff, not only an isolated permission decision:

- **Inputs:** mixed private/shareable project material, prior work artifacts, execution dependencies, a recipient, a purpose, owner-provided constraints, and a budget for clarification.
- **Output:** a reviewable sharing proposal containing evidence, executable resources, permitted actions, and unresolved decisions. Only owner-authorized proposals become active capabilities.
- **Evaluation:** unseen follow-up questions and verification tasks, across recipients and projects, after preparation has finished.
- **Objectives:** complete useful work, avoid unauthorized disclosure/actions, reduce unnecessary authorized exposure, and reduce the owner's total effort.

For example, a supervisor needs the evidence and approved rerun behind a result, but not unrelated personal notes or another collaborator's unpublished work. A later question about a changed parameter may require an additional dependency. The system must distinguish a missing safe dependency from a genuinely new permission request, without quietly broadening access.

The difficult coupling is that evidence, executable dependencies, and authorization affect each other. A runnable package may expose more than the reviewer needs; a carefully filtered explanation may omit what is required to verify it. Unseen follow-ups make a package that succeeds on one scripted demonstration inadequate.

### Candidate method to investigate

Use a project representation that connects claims, supporting artifacts, runnable checks, dependencies, and authorization evidence. Propose a share, test its permitted capabilities, identify unresolved gaps, and select clarification questions that most improve expected follow-up coverage without violating explicit constraints.

This is a **method hypothesis**, not a novelty claim. Dependency graphs, policy generation, and active elicitation already exist. A paper needs an additional technical insight, or a rigorous new empirical finding, showing why their straightforward composition fails and how the proposed approach addresses that failure.

Owner intent must not be inferred into consent. Explicit prohibitions remain hard constraints; uncertain grants remain inactive until resolved. Runtime enforcement remains outside the language model.

### Other publication paths

- **Usable security/HCI:** show how owners express sharing intent, where scope previews mislead them, and which interaction design reduces effort while preserving understanding and correct authorization. A satisfaction survey alone is insufficient.
- **Benchmark or measurement:** document a reproducible failure involving preparation, actual owner intent, and held-out follow-ups that existing benchmarks do not capture. Adding a sandbox or changing role names is insufficient.
- **Small-model optimization:** investigate only after a measured bottleneck exists. Compare safety, useful completion, calibration, latency, and total cost, including preparation and model-training costs. Fine-tuning itself is not the contribution.

## 5. Tensions in v0.1 that must remain explicit

1. **Low owner effort is not implemented by the P0 plan.** P0 uses explicit resource selection and reviewed context. Measure initial host setup separately from preparation and repeated interruptions for each handoff.
2. **Owner-hosted execution is not arbitrary environment capture.** P0 assumes approved files, images, and immutable assets; it does not capture live RAM, GPU state, or every dependency on a personal machine.
3. **The first meaningful workload must fit CPU-only P0.** If early users mainly need GPU experiments, revisit the roadmap using evidence rather than demonstrating a task they would not actually review.
4. **Approved runnables must support real questions.** A hard-coded replay demonstrates execution but not useful follow-up. Include unseen requests and dependency failures in evaluation.
5. **Runtime-readable material is treated as disclosed.** P0 does not solve private computation over undisclosed inputs, semantic non-inference, or cumulative inference across all possible questions.
6. **Few prompts do not necessarily mean low effort.** Count preview reading, corrections, scope debugging, and later rescue work. Independent owner-intent checks are needed to detect uninformed acceptance of suggested defaults.

These are refinements to claims and validation, not a request to expand P0 into a general-purpose agent platform.

## 6. Experiments to build into development

### A. Validate the workflow before automation

Run exploratory pilots with approximately 5–10 owner/reviewer pairs using synthetic or explicitly approved projects. Choose CPU-based analysis or code/result review. Each reviewer should ask some questions not supplied to the preparation process. This sample is for discovering failures, not making population-level claims.

Compare with the owner's existing handoff practice and a competently prepared container/workspace with manual permissions. Where practical, test the closest product using the same task. If a comparator cannot run the task, document the configuration and mismatch rather than assigning it a zero score.

### B. Separate preparation from execution

Record where failures originate: wrong owner-intent interpretation, missing evidence, missing dependency, incorrect grant, broken enforcement, or collaborator/model execution. Otherwise an improved language model can be mistaken for a better sharing method.

### C. Use strong composed research baselines

After a manual path works, compare on a shared execution harness:

- Explicit manual selection and a fixed policy template.
- A general model proposing a share with the same tools, information, and budget.
- An adapted AuthBench-style sufficiency/tightness pipeline for supported file permissions.
- Dependency packaging plus recipient rules, programmable policy updates, and an active clarification policy.
- An owner-reviewed reference package as an empirical comparison point, without calling it a uniquely minimal oracle.

Report adaptations and unsupported scopes. Do not imply that any single cited paper implements this complete baseline. Hold runtime and execution agent constant when comparing preparation methods. Include ablations of dependency reasoning, question selection, and recipient conditioning.

### D. Measure separate outcomes

| Outcome | Measurement |
| --- | --- |
| Useful completion | Correct answers with supporting evidence; successful permitted reruns and follow-up work |
| Unauthorized exposure/actions | Violations against independently established owner constraints; severity and attack conditions |
| Unnecessary authorized exposure | Extra disclosed material within the allowed boundary; report sensitivity and content, not only file count |
| Owner effort | Setup time, preparation time, preview/review time, corrections, questions, and later interventions |
| Operational cost | Preparation/startup latency, execution cost, provider use, and host resources |
| Boundary understanding | Whether owners and reviewers correctly understand what is accessible and executable |

Do not combine these into a single score that hides safety failures. Refusing everything can reduce leakage while destroying the product's purpose. Zero observed violations in a finite test is not a general privacy guarantee.

### E. Prevent optimistic evaluation

Split by owner, project, and follow-up task family. Keep evaluation questions unavailable during package preparation. Include benign requests, malicious instructions, mixed-sensitivity files, dependency changes, recipient changes, and attempts to exceed action or resource budgets. Obtain participant consent and keep private pilot material out of public benchmark releases.

## 7. Decision gates

| Decision | Evidence needed |
| --- | --- |
| Continue the product | Reviewers accomplish valuable work; owners see a meaningful reduction in total handoff burden against a credible alternative; boundaries pass targeted tests. |
| Invest in the proposed research method | The same preparation/authorization failure recurs across projects, and a strong composition of existing methods does not solve it adequately. |
| Claim a method contribution | A distinct mechanism yields reproducible improvement against strong baselines, with useful completion and owner effort assessed at comparable safety levels. |
| Claim a measurement/HCI contribution | Rigorous evidence reveals a previously under-characterized failure or interaction trade-off and supports a useful, generalizable design conclusion. |
| Narrow or pivot | Most value comes from commodity workspace sharing; owners still do most preparation; or apparent gains disappear with stronger baselines. |

Set numeric targets before the main comparative study, based on pilot workloads and the acceptable risk for that setting. Do not retrospectively choose thresholds to fit favorable results.

## 8. Recommended narrative

**Product:** Prism helps an owner prepare existing private agent work for a named collaborator to inspect, verify, and continue within explicit boundaries on an owner-controlled host.

**Research hypothesis:** Preparing such a handoff requires jointly balancing executable sufficiency, recipient-specific authorization, and owner effort under uncertain follow-up demand. Prism will test whether this coupling creates failures that current methods, even when combined, do not adequately address.

**Current claim:** a worthwhile product experiment with credible research opportunities. Claims of being the first, having no competitors, delivering stronger security, or already possessing a strong paper contribution are not supported by the present evidence.

## 9. Agent behavior as a continuing investigation (2026-09-17)

The owner has emphasized agent behavior as a product refinement priority and a possible research direction. The [behavior plan](agent-behavior.md) turns this into proposed observable contracts and experiments, starting from the actual M1 failures rather than treating model/tool connectivity as a finished agent.

Keep two experimental subjects distinct. A collaborator agent chooses evidence, permitted actions, clarification and stopping decisions within an approved share. A later owner-side preparation assistant proposes context, dependencies and questions for owner review. Improving the former does not establish that the latter prepares a better share, or vice versa. Freeze the package/runtime for collaborator-policy comparisons; freeze the collaborator agent for preparation-method comparisons.

The candidate behavior question is whether explicit connections among claims, evidence, actual run state and unresolved gaps improve unseen follow-up completion while reducing unnecessary execution and owner intervention under the same enforced permissions. This is a hypothesis to test against the current agent and strong tuned alternatives. Preserve negative results, hold out projects and task families, and separate semantic correctness, usefulness, boundary outcomes and cost. No new literature claim, method contribution or measured advantage is established by this addendum.
