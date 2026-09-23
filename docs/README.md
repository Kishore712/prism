# Prism Documentation

**Latest interface refinement (2026-09-19):** The authorized [general-purpose handoff workspace](general-handoff-workspace.md) makes conversation the task entry point and groups supporting activity/results and capabilities. Dedicated experiment launch forms are removed; the fixed synthetic action remains a limited test fixture. Backend permissions are unchanged.

**M2.2 status (2026-09-20):** The separately authorized [explicit project import increment](m2-project-import.md) is implemented and validated in local development on `prototype/m2-project-import`, awaiting owner review. The [validation record](validation/m2-project-import.json) records 98/98 backend tests, 11/11 focused project checks, 9/9 bounded runtime checks, the final 11/11 M1 regression, frontend checks, browser evidence, preservation audits, and limitations. This remains local-development scope and is not a private-pilot gate.

**M2.3 and order 4 status (updated 2026-09-23):** [Reference Linux runtime integration](m2-linux-runtime.md) passed real cloud acceptance at 46/46 checks with `pilot_ready=false`; owner acceptance remains pending. The local order 4 identity service passed 174 tests. Its private Google OIDC/Tailscale HTTPS service completed bounded recipient checks on synthetic Paired and Document release handoffs, including fresh scoped sessions, denied unauthorized access, a permitted cited answer, a fixed Kata `json-check` run, and revocation followed by denied access. The current live demo uses a temporary $10.00 aggregate model reservation ceiling (1,000 cents); the previous $0.80 reservation remains recorded, leaving $9.20 at that checkpoint before later demo calls; check the live interface for the current remainder. A test-recipient grant was recorded with expiry 2026-09-24 03:35:54 PDT; check the live interface for current access. An earlier recorded Kata run completed with `io.containerd.kata.v2` in 3.979 seconds, exit 0, and confirmed cleanup. The app-side ceiling is not a hard provider billing cap and remains until the user explicitly ends the demo. These checks do not establish full order-4 acceptance, broad security, or pilot readiness. See the [end-to-end live demo SOP](live-demo-sop.md), [guest service guide](m2-recipient-guest-service.md), and [sanitized live recipient record](validation/m2-recipient-live-identity.json).

## Start here

The [maintained visual roadmap](roadmap.md) shows what is implemented, what is awaiting owner acceptance and what remains planned. Start there for current delivery status; dated historical notes elsewhere retain their original scope.

The confirmed [owner project chat and reviewed conversation handoff direction](m2-owner-workspace.md) is the first M2 workstream. Owner chat (M2.1a) and subsequently authorized [manual reviewed-handoff preparation (M2.1b)](m2-reviewed-handoff.md) are implemented in the synthetic local scope. M2.1b awaits owner review. The subsequently authorized [M2.1c contextual continuation](m2-context-continuation.md) is implemented and awaiting owner review; approved versions now initialize fresh collaborator sessions. See its live failure/recovery evidence and current limits.

[Product Design: Secure Agent Sharing and Handoff](product-design.md) is the current product specification. It covers the product narrative, users, owner-hosted sandbox mode, privacy and permission boundaries, collaborator workflows, MVP scope, evaluation, and delivery roadmap.

The [two delivery modes and implementation priority](product-design.md#delivery-modes) addendum distinguishes online sharing on owner-controlled resources from a later independent handoff to collaborator-controlled resources. **Complete and validate online sharing first.** Independent handoff is documented future work, not an implemented feature or a new P0 requirement.

The design is ready for collaborative review. M0 development tools and the reference Linux/Kata feasibility spike passed their recorded synthetic checks. The broader product security/performance properties and private-pilot release gates remain requirements to validate. See the [M0 guide](m0-host-validation.md) for the exact implemented and tested scope.

## Suggested reading order

1. [Product definition](product-design.md#product-definition), [scenarios](product-design.md#reference-scenarios), and [scope](product-design.md#scope-and-priorities).
2. [Authorization](product-design.md#authorization), [resource policy](product-design.md#resource-policy), and [privacy contract](product-design.md#privacy-and-threat-model).
3. [Architecture](product-design.md#system-architecture), [local snapshots](product-design.md#snapshots-and-local-resources), and [lifecycle](product-design.md#lifecycle).
4. [Release gates](product-design.md#evaluation-and-release-gates), [implementation backlog](product-design.md#roadmap), and [open decisions](product-design.md#decisions-and-open-questions).

For the delivery-mode distinction, also read the [package, trust boundaries and deferred acceptance](product-design.md#delivery-modes) and [agent initialization in each mode](agent-design.md#agent-delivery-modes). Owner-local and development-server hosting are both online-sharing placements; Inspect/Verify/Continue are separate capability choices.

## Implementation handoff

Begin with M0: inspect the current repository, validate the reference Linux host and isolation adapter, and record the first architecture decisions. Build a manually usable sharing path before adding automatic scope inference. The initial demo must include approved execution as well as evidence chat.

Do not treat the proposed directory structure, configuration example, host support, or runtime capabilities as already implemented. Follow [CONTRIBUTING.md](../CONTRIBUTING.md) when making changes.

## Research background

- [Product Differentiation and Research Revalidation](product-research-validation.md) — the 2026-09-17 review of v0.1, additional close precedents, and experiments needed to establish product value and research contributions.
- [Related Works Wiki](https://github.com/Kishore712/prism/wiki/Related-Works)
- [Innovation Assessment Wiki](https://github.com/Kishore712/prism/wiki/Innovation-Assessment)

The Wiki provides research context. The product design records the current delivery direction and explicitly distinguishes existing capabilities from proposed differentiation.

## Documentation policy

Repository and Wiki documentation is English-only. Local translated research materials stay outside this repository. Keep requirements, implemented behavior, test results, and research hypotheses clearly distinguished.

## Agent implementation and development guidance

- [Current Agent Implementation, Architecture, and Gaps](agent-current-implementation.md) describes the delivered owner/collaborator agent paths, authorization and execution boundaries, bounded validation evidence, and remaining work as of 2026-09-23.
- [Agent Behavior: Refinement and Research Plan](agent-behavior.md) proposes an ongoing behavior workstream: grounded answers, action selection, gap diagnosis, recovery and bounded help requests, with repeatable evaluation before research claims. B0/B1 were originally proposed as M1 refinements; the current roadmap scopes them into the M2 proposal without an extra prerequisite stage. No new implementation or experiment result is implied.
- [Agent Implementation Design](agent-design.md) extends the product specification with the fresh collaborator agent, reviewed context, model and tool integration, session state, evidence provenance, and separately reviewable delivery steps. Owner and collaborator agents are implemented for the configured synthetic project; see M2.1c for the current scope and live failure/recovery evidence.
- [Instructions for Development Agents](../AGENTS.md) records repository preservation, confirmation gates, safe development practices, security requirements, and validation expectations, with links to official guidance. These instructions govern repository work, not collaborator-agent permissions.
- [M2.1: Owner Project Chat and Reviewed Conversation Handoff](m2-owner-workspace.md) specifies owner work, exact context review, collaborator continuation, implementation order, boundaries and acceptance. A working owner agent and a future automated share-preparation assistant are separate capabilities.
- [M2.2: Explicit Project Import and a Bounded JSON Action](m2-project-import.md) specifies the supported manifest, operator preconditions, exact file review, privacy/model disclosure, fixed action and local-development limits. It is implemented and locally validated, with owner review pending; see the [validation record](validation/m2-project-import.json).
- [M2 order 4: Named Remote Collaborator Invitations](m2-recipient-invitations.md) records the local fixed-HTTPS OIDC identity-service, exact issuer/`sub` binding, invitation and grant boundaries. The [private setup checklist](m2-recipient-private-setup.md) records provider and tailnet preparation without private values. Bounded live recipient sessions, denial checks, model questions, and a Kata `json-check` run have completed; full order-4 acceptance and pilot readiness remain pending. See the [local validation record](validation/m2-recipient-invitations.json), [sanitized live recipient record](validation/m2-recipient-live-identity.json), and [end-to-end demo SOP](live-demo-sop.md).
- [Private live demo SOP](live-demo-sop.md) walks the owner and USC test identity through project context selection, exact-version approval, a 24-hour invite, scoped review, real model question, Kata `json-check`, denial checks, and read-only Cloud Shell evidence.
- [Private guest identity-service guide](m2-recipient-guest-service.md) describes guest configuration, direct-TLS systemd lifecycle, immutable release update, and the current private demo state. Its checks do not establish full order-4 or pilot acceptance.

## Implemented development tools

- [M2.1b manual selection, exact review, approval and revision](m2-reviewed-handoff.md), with [50 focused checks and browser evidence](validation/m2-reviewed-handoff.json). No new inference or runtime execution was needed; the browser copied an existing actual result.
- [M2.1a owner chat walkthrough and delivery record](m2-owner-workspace.md#try-owner-project-chat), with [automated and live evidence](validation/m2-owner-chat.json). This supersedes historical owner-chat-unimplemented notes; it does not establish the complete M2.1 handoff journey or pilot readiness.
- [M1 interface refresh, navigation and acceptance checks](ui-refresh.md). The owner workspace and conversation-first reviewer interface are implemented and awaiting owner review. This revision does not start M2.
- [M1 local prototype, Agile backlog, setup and current acceptance status](m1-local-sharing.md). Snapshot sharing, evidence browsing, real model conversation and actual synthetic verification have been exercised. M1 awaits owner review, with documented failed turns and limitations. This newer status supersedes the historical agent-unimplemented description above.
- [Recorded M1 live model and configured-service boundary evidence](validation/m1-live-model.json), including failed attempts, actual runs and local budget reservations.
- [Recorded M1 real development-job evidence](validation/m1-development.json), separate from model doubles and live model acceptance.

- [M0 setup, acceptance, and limitations](m0-host-validation.md).
- [ADR 0001: development runtime and pilot boundary](decisions/0001-m0-runtime.md).
- [Recorded synthetic runtime evidence](validation/m0-development.json).
- [Recorded reference Linux/Kata evidence](validation/m0-linux-kata.json).
- [GCP reference-host setup and validation status](gcp-m0-host.md).
