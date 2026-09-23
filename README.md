# Prism

**Latest interface refinement (2026-09-19):** The authorized [general-purpose handoff workspace](docs/general-handoff-workspace.md) makes conversation the task entry point and groups supporting activity/results and capabilities. Dedicated experiment launch forms are removed; the fixed synthetic action remains a limited test fixture. Backend permissions are unchanged.

**M2.2 status (2026-09-20):** The separately authorized [explicit project import increment](docs/m2-project-import.md) is implemented and validated in local development on `prototype/m2-project-import`, awaiting owner review. Backend, runtime, frontend, and browser evidence are recorded in the [validation record](docs/validation/m2-project-import.json). The scope remains one strict `.prism-project.json` format, explicit file selection, direct file-only handoff, and the bounded `json-check` action; private-pilot readiness remains false.

**M2.3 and order 4 status (updated 2026-09-23):** [Reference Linux runtime integration](docs/m2-linux-runtime.md) passed real cloud acceptance at 46/46 checks with `pilot_ready=false` and `model_calls=0`; M2.3 owner acceptance and pilot readiness remain pending. The local order 4 identity-service passed its recorded 174-test validation. Its private Google OIDC/Tailscale HTTPS service has completed bounded recipient checks on synthetic Paired and Document release handoffs: exact-identity invitations and fresh scoped sessions, denied unauthorized evidence/API access, a permitted cited answer and Kata `json-check` run, and grant revocation followed by denied access. The current live demo uses a temporary $10.00 aggregate model reservation ceiling (1,000 cents); the previous $0.80 reservation remained recorded at the time of the change, leaving $9.20 at that checkpoint before later demo calls; check the live interface for the current remainder. A test-recipient grant was recorded with expiry 2026-09-24 03:35:54 PDT; check the live interface for current access. An earlier recorded Kata run completed on `io.containerd.kata.v2` in 3.979 seconds with exit 0 and cleanup confirmed. The ceiling is app-side, not a hard provider billing cap, and stays until the user explicitly ends the demo. These checks do not establish full order-4 acceptance or broad security; the VM remains RUNNING with no automatic stop, fixed on-demand IAP TCP/22 management access, and no public HTTPS ingress. See the [live demo SOP](docs/live-demo-sop.md), [guest service guide](docs/m2-recipient-guest-service.md), and [sanitized live recipient validation](docs/validation/m2-recipient-live-identity.json). `pilot_ready=false`.

Prism is an early-stage project for sharing and handing off personal agent work with explicit privacy and execution boundaries. The proposed product lets named collaborators ask questions, inspect evidence, rerun approved experiments, and continue limited work in isolated environments on the owner's computer or development server.

## Status

See the [visual roadmap and current delivery status](docs/roadmap.md) for implemented capabilities, acceptance gates and future work. It is updated with each increment or milestone handoff.

Product design is ready for collaborative review. M0 provides host diagnostics and real synthetic runtime selftests; its reference Linux/Kata feasibility checks passed on the recorded configuration. M1 now has a synthetic loopback sharing interface, reviewed immutable versions, independent local demo sessions, evidence browsing and actual bounded verification. An explicitly configured real model has answered evidence questions and requested an actual verification run. The limited live check also recorded failed turns and a provenance wording issue; see the M1 record rather than assuming every question succeeds. M1 is ready for owner review, not yet accepted. Private-pilot readiness remains false. The project is working toward an open-source prototype.

Start with the [M0 setup and acceptance guide](docs/m0-host-validation.md) to run the current development tools and understand the tested scope and remaining limits.

For the browser prototype, follow the [M1 setup, Agile backlog and acceptance guide](docs/m1-local-sharing.md). The local demo is not an external invitation service or a production security boundary. No real model reply is simulated when a provider credential is missing.

The subsequently authorized [M2.1a owner project chat](docs/m2-owner-workspace.md#try-owner-project-chat) is implemented and awaiting owner review. Owner now opens on a private project conversation with evidence, persisted history and actual bounded runs. Four live turns included two successful answers and two structured-output failures; the actual seed-7 run completed. This checkpoint preceded the reviewed-handoff increment described below; collaborator continuation was subsequently implemented in M2.1c. The synthetic scope and existing aggregate model allowance are unchanged.

## Product design

The subsequently authorized [M2.1b reviewed-handoff preparation](docs/m2-reviewed-handoff.md) is also implemented and awaiting review. Owners can manually select completed conversation excerpts, files and historical results, then approve their exact frozen content. The subsequently authorized [M2.1c continuation](docs/m2-context-continuation.md) now opens them in fresh collaborator sessions with approved background and separate historical/new execution references. It is ready for owner review: 54 focused tests and a live two-turn recovery demonstration (one failed final answer, one successful comparison, one actual new run). Automatic boundary recommendations remain deferred.

Start with the [product design](docs/product-design.md) for the user journeys, permissions, owner-hosted sandbox model, MVP scope, and delivery roadmap. The [documentation index](docs/README.md) provides a reading order and implementation handoff.

Prism's intended [two delivery modes](docs/product-design.md#delivery-modes) are online sharing on owner-controlled resources and a later independent handoff to collaborator-controlled resources. **Online sharing is the implementation priority.** The independent package/export/import workflow is planned, unimplemented and outside current P0 delivery.

Research background is available in the [Related Works Wiki](https://github.com/Kishore712/prism/wiki/Related-Works) and [Innovation Assessment Wiki](https://github.com/Kishore712/prism/wiki/Innovation-Assessment).

## Collaboration

- Use a separate branch for each change.
- Open a pull request before merging into `main`.
- Keep unfinished notes and local artifacts out of the repository.
- Do not commit credentials, private conversations, personal data, or restricted research material.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the working conventions.

## License

No license has been selected yet. Until one is added, the project should be treated as private and all rights reserved.
