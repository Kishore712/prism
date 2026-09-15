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
