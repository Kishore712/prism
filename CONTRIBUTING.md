# Contributing to Prism

## Working together

1. Create a short-lived branch from `main`.
2. Make one focused change per branch.
3. Open a pull request explaining what changed and why.
4. Ask the other collaborator to review substantial changes before merging.
5. Merge only after resolving comments and basic checks.

## Branch names

Use a short category and description:

- `research/<topic>`
- `docs/<topic>`
- `prototype/<feature>`
- `fix/<issue>`

For example: `research/sharing-workflows`.

## Commit messages

Write short, action-oriented messages, such as:

- `Add initial research questions`
- `Document projection assumptions`
- `Prototype snapshot export`

## Data and security

- Use synthetic or explicitly approved data for experiments.
- Never commit API keys, access tokens, credentials, private agent histories, or personal information.
- Keep large generated files and local experiment output outside Git unless the team agrees they are needed.
- If sensitive data is committed accidentally, notify the other collaborator immediately and rotate any exposed credential. Removing the file in a later commit is not sufficient.

## Design material

Design documents should be added only after they are ready for collaborative review. Drafts can remain outside the repository until then.

Repository and Wiki documentation must be in English. Maintain translated research material outside the repository. Distinguish proposed requirements from implemented behavior, measured results, and research hypotheses.

## Agile development

Keep a small, ordered milestone backlog with user stories and observable acceptance criteria. Implement a vertical increment, run its relevant positive and denial-path checks, inspect the usable result, and adapt the remaining backlog to what was learned. Do not count code written or tests mocked as user value delivered.

An increment is done only when its behavior, necessary verification, user-facing states, and English usage/limitation notes agree. Record blocked dependencies explicitly. Report at the owner's agreed milestone boundary and obtain authorization before starting the next milestone; Agile iteration does not waive security or scope approvals.

Maintain the [visual roadmap](docs/roadmap.md) as part of that definition of done. At every increment or milestone handoff, and whenever scope, validation, blockers or owner acceptance changes, update its date, diagram/status, evidence links, next action and short update log alongside the active backlog. Keep implemented, verified, owner-accepted and planned states distinct. This maintenance requirement does not authorize starting a later milestone or scheduled background work.
