# M2.2: Explicit Project Import and a Bounded JSON Action

**Status:** implemented and validated in local development on `prototype/m2-project-import` (implementation 2026-09-19; acceptance evidence 2026-09-20), awaiting owner review. See the [validation record](validation/m2-project-import.json) for the checks and their limits. Private-pilot readiness remains false.

M2.2 lets the local operator configure one or more explicitly supported project manifests, lets the owner choose a configured source and explicit files, and prepares a direct file-only handoff or a private project-chat context from that selection. It adds one fixed action, `json-check`, for checking approved selected JSON. It does not turn project import into arbitrary code execution or a private-pilot capability.

## Scope and status boundary

The operator supplies repeatable manifest paths when starting the local service. The option is:

```text
--project /path/to/.prism-project.json
```

For example, the new English `document-handoff` source, which contains synthetic data, can be configured in the local development start:

```text
uv run --no-editable prism demo --data-dir .prism-demo \
  --project "$PWD/examples/document-handoff/.prism-project.json" \
  --allow-openai --openai-key-file .prism-demo/prism-openai.key \
  --model-budget-cents 300
```

This is the repository's existing local demo launcher. The `--project` option is repeatable; repeat it with additional absolute manifest paths when more than one configured source is needed. Keep the existing `--data-dir .prism-demo` (or the operator's existing dedicated data directory) when exercising the local demo so the historical model ledger is preserved. The model options above reuse the already configured local demo route; the command reads the explicitly named credential file and makes no claim about its contents. The checked-in English synthetic `document-handoff` example is separate from the older fixed `.prism-demo` fixture so that the new source-selection path does not reuse the old fixture as evidence.

**Bounded live walkthrough authorization (2026-09-20):** The owner permits a real-model walkthrough on the synthetic `document-handoff` data using the same `.prism-demo` database, configured project, and configured key, with the local allowance increased from $2 to $3. This is an additional $1 only and does not authorize unlimited spending, cloud activity, or broader/private data. The historical [M2.2 validation record](validation/m2-project-import.json) remains unchanged: it records zero model calls within the prior $2 scope and $1.90 reserved.

The owner project picker first chooses one configured source. For a New Share, every imported project file is initially unselected; the owner then explicitly selects the files to include. This New Share selection is independent of file choices in the owner's private project session. A source with only `README.md` selected can still support owner chat inspection; selecting a file does not imply that an action is enabled. A private project-chat turn can use only its own explicitly selected context after the applicable model-consent acknowledgement. A direct file-only handoff from a selected project does not require a model call. In either case, selected bytes are copied into an immutable version; later source edits do not modify an existing version.

This is a local-development feature. The development runtime, loopback service, synthetic examples, and focused checks do not establish a remote pilot, authenticated external sharing, integrated Linux/Kata isolation, or a production security boundary. M2.3 is the separately reviewed next increment for reference-Linux runtime integration.

## Supported manifest format

Each configured source is a regular local file named `.prism-project.json`. Its strict top-level shape is:

```json
{
  "schema": 1,
  "id": "document-handoff",
  "title": "Synthetic document handoff",
  "files": ["README.md", "report.json"],
  "action": "json-check"
}
```

The supported fields are:

| Field | Requirement |
| --- | --- |
| `schema` | The integer `1`. |
| `id` | A slug identifying this configured source. |
| `title` | A string of at most 120 characters shown for source selection. |
| `files` | One to eight explicit relative UTF-8 filenames. |
| `action` | Either `"json-check"` or `null`. |

The object is strict: unknown fields, missing fields, wrong types, invalid counts, invalid names, and unsupported action values are rejected. The manifest is the complete file inventory for this source. Prism does not recursively scan its parent directory, infer additional files, or let a parent or nested manifest override it. A selected source's root is the directory containing its manifest.

`source_kind` is recorded by the application rather than added to this strict manifest. Configured local sources, including the `document-handoff` acceptance example loaded through its manifest, are `local-project`. The legacy paired-evaluation fixture is `synthetic`. These values identify how a source entered Prism and do not expand the file selection.

The configuration file is limited to 16 KiB. Each selected file is limited to 32 KiB, and the sum of the selected file bytes in the approved immutable version is limited to 96 KiB. The file list must contain at least one and at most eight entries. Names must remain relative to the manifest root and must be valid UTF-8. Absolute paths, traversal, normalized path escapes, duplicate or unsafe entries, and references outside the selected root are rejected.

Each filename is limited to 240 characters and at most eight path segments. These limits apply to every explicit `files` entry before materialization.

Symlinks, hard links, special files, and other non-regular-file targets are rejected. The importer does not follow links or copy an entire directory. These checks apply before the owner can approve a selection.

## Owner selection and disclosure

The picker exposes only configured project sources. The local operator and owner project scope may inspect declared source files to make the selection. New Share starts with all imported project files unselected. After a source is chosen, the owner explicitly selects the listed files. The collaborator and any model route attached to that reviewed share receive only the approved copied bytes and context explicitly selected for that version. The owner may separately select a private note or other declared file for the owner's private session and, after consent, send it to the owner model; that private-session choice does not add the file to a New Share. Unselected files, source paths, directory listings, repository history, private notes, credentials, and hidden owner conversation state are not disclosed to a collaborator through this flow.

Selecting a file does not make every fact safe to disclose. If an owner-written summary or selected chat excerpt contains a private fact, that fact is part of the reviewed disclosure even when its originating file is omitted. Review the visible text and bytes together.

The source remains unchanged. A later edit, replacement, or deletion of a source file cannot change a previously approved version. A new selection and approval are required to include changed bytes. Historical results, when selected through the existing reviewed-handoff flow, remain labelled as historical owner results and are separate from new activity.

The direct file-only path has no model requirement. For a private project-chat turn, the configured model route is a separate data recipient: the UI must disclose the endpoint/operator, model identifier, and selected context categories before consent. Missing model configuration makes chat unavailable; it does not cause a simulated answer or broaden the file selection. Model output is untrusted content and does not authorize a new file, action, tool, package, or host access.

## The fixed `json-check` action

`json-check` is the only M2.2 action. It consumes approved selected JSON file content and reports whether each supported selected input is valid JSON. The action receives the approved selection through the bounded runtime; it does not discover files from the project root.

The 96 KiB snapshot cap is not the action's transport or parser limit. Before creating an executable version, Prism preflights the action transport. The selected JSON action payload is base64 encoded and is limited to 100 KiB after encoding. A file set can therefore fit in an approved immutable version while still being rejected by the action transport preflight. This distinction must remain visible in the UI and result record.

The action supports JSON up to depth 80 and integer values up to 256 digits. It must distinguish malformed JSON from JSON that is syntactically valid but outside these supported parser limits. A failed check uses `valid: false` and an error reason of `syntax`, `unsupported_depth`, or `unsupported_number`; these outcomes are not arbitrary execution failures and do not trigger a fallback action.

The failure classification is represented conceptually as:

```json
{"valid": false, "error": {"reason": "unsupported_depth"}}
```

The reason changes to `syntax` for malformed JSON and to `unsupported_number` for an integer beyond the supported digit bound.

The action contract is deliberately fixed:

- no arbitrary project script, shell command, executable path, or setup code;
- no package installation or dependency download;
- no automatic archive extraction or repository discovery;
- no task-network access;
- no host-directory mount and no source write-back;
- no model call is needed to perform the check;
- the agent's `check_json` tool accepts only the approved JSON action and its bounded input.

The existing bootstrap capability remains a fixture-only development action. It is not a general project-import action and is not silently substituted for `json-check`.

The bounded runtime contract for this increment is a pinned image in the existing development container runtime with network disabled and no host mount. The implementation and validation must confirm these limits:

| Limit | M2.2 bound |
| --- | ---: |
| Memory | 128 MiB |
| CPU | 0.5 CPU |
| Processes | 32 PIDs |
| Wall time | 10 seconds |
| Writable scratch | 8 MiB |
| Captured output | 64 KiB |

These values describe the approved contract under review; they are not evidence that the local runtime is suitable for an external pilot. A missing or unsupported runtime capability must block the action instead of silently falling back to a broader host execution path.

## Preconditions and local workflow

Before starting the service, the operator must:

1. Provide at least one explicitly named `.prism-project.json` with repeatable `--project` options.
2. Keep each manifest and its listed files in a local source root that the operator is authorized to disclose. M2.2 acceptance uses the new authorized English synthetic example; configuring other local files is an operator capability and is not acceptance evidence about private workspaces.
3. Use the supported schema and regular UTF-8 files; malformed or unsafe sources fail before selection.
4. Treat the project picker as a disclosure step: source choice and file choice are both explicit.
5. Review the exact copied bytes and the declared action before approving the version.

The owner can then choose one of the supported paths:

- **Direct file-only handoff:** approve selected files for a fresh collaborator context. No model call is required.
- **Private project chat:** acknowledge the selected-context model disclosure, then ask the owner agent to work from that private-session selection. Chat history and model outputs remain governed by the existing owner-project scope; the selection does not silently populate New Share.
- **Bounded JSON check:** approve the `json-check` capability and submit only the supported JSON input. The result appears in the unified activity/results records with its action and output limits.

The action result is a new execution record. It must not overwrite source files or be presented as a historical owner result. A failed or unavailable check remains distinguishable from a successful result.

## Acceptance boundary

Acceptance is organized around the user-visible behavior and its denial paths:

| Area | Required demonstration | Boundary to preserve |
| --- | --- | --- |
| Operator configuration | Start with one manifest and with multiple repeatable `--project` options; choose a configured source in the picker. | Unconfigured sources, malformed JSON, extra keys, invalid counts, unsafe names, and duplicate/ambiguous paths are rejected. |
| Explicit selection | Start with all imported project files unselected, select one or more listed files, and review the resulting disclosure. README-only selection remains inspectable in owner chat. | No recursive scan, parent/nested manifest override, unlisted file, source path, or excluded canary is disclosed to a collaborator. |
| Immutable version | Change the source after approval and inspect the earlier version. | The earlier bytes and action do not change; changed content needs a new reviewed version. |
| Direct handoff | Prepare a selected file-only handoff without a model call. | No model consent or provider request is invented for this path; no owner-private history is inherited. |
| Private chat context | Obtain consent for selected context and inspect the owner chat's assembled selection. | Only selected context is eligible; model output cannot expand scope or grant a tool. |
| `json-check` | Preflight the base64 payload before executable-version creation, run the fixed action on approved selected JSON, and inspect the stored result. | Unapproved JSON, payloads over 100 KiB base64, depth over 80, integers over 256 digits, arbitrary scripts, package setup, network, host mounts, source writes, and out-of-bound resources fail with bounded reasons. |
| Runtime limits | Confirm the pinned existing development runtime applies the documented bounds. | A missing capability blocks activation; container-only development evidence does not pass the private-pilot gate. |

Validation is complete for the recorded local-development scope: the backend suite passed 98/98, focused project checks passed 11/11, the bounded project runtime check passed 9/9, the restored old bootstrap path passed its final 11/11 regression, targeted Ruff passed, and the frontend formatting/build checks passed. Browser acceptance covered two project sources, zero-default New Share selection, README-only owner chat, project switching, reviewed exclusion of the private note, fixed JSON limits/hash visibility, approval, and a fresh reviewer session. No live model JSON response is claimed; model calls remained at zero. The separate [validation record](validation/m2-project-import.json) preserves the evidence, maintenance audit, and limitations.

M2.2 awaits owner review. M2.3 reference Linux runtime integration is now separately authorized and in progress under its [runtime guide](m2-linux-runtime.md); its implementation and acceptance evidence remain pending.

## Live walkthrough · 2026-09-20

Using the same `.prism-demo` database, configured project, and approved key, a bounded real-model walkthrough created version `fa19cf5ccd6d432f95b0820b18fee00d` with three selected files and the private file excluded, then started reviewer session `1458c598bf33499d8840d79e6f9ce15e`. Three turns ran: two final answers failed because source references were incomplete (`3d45a5c47e8549bb93d5d91c25b43645` after a successful JSON check run `9afd96ef2cee4359ae18837e77b9738c`, and read-only follow-up `42517682de144558b80cadd7d15de4b4` requiring a historical result reference); a third file question completed and cited `docs/release.md` lines 1–3 (`250c8d350f904ddbbfc20aeb1b18e38b`). The JSON run took 0.262 seconds, exited 0, cleaned up, and reported `valid: true`. This is real model/tool integration evidence, not complete live-answer acceptance.

The walkthrough used one run and three turns (two failed, one completed). The ledger moved from 38 to 45 dispatches and from 190 to 225 reserved cents within the approved 300-cent total; 75 cents remained as an application reservation, not an actual billing statement. A pending access request remained visible to the owner without changing permissions. Revoking the new version denied reviewer access after reload and removed it from the list; the prior `c8b8d21f7df4` version remained available for the existing user-experience record. The owner’s collaborator-conversations view still shows all three turns in session `1458c598bf33499d8840d79e6f9ce15e` after revocation.

## Narrow run-reference repair · 2026-09-20

The owner authorized and the parent completed a follow-up on `fix/run-reference-answers` after the walkthrough's two reference-classification failures. The [validation record](validation/run-reference-answers.json) records 54 focused tests, targeted Ruff, strict validation, one finite output correction with function tools hidden, side-effect protection, and real model/container evidence. `new_run` answers now cite completed runs in the current session while `historical` references remain limited to approved owner context. The limits of four requests, eight tools, 90 seconds, and the approved 300-cent budget remain unchanged. This validates the recorded repair path but does not guarantee every model wording or citation pattern; M2.3 remains separate and unstarted.
