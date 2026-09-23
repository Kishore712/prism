# M1 Interface Refresh

Status: implemented and checked on 2026-09-17; awaiting owner acceptance. This is an owner-requested M1 revision, not the start of M2. The existing local-demo security profile, model route, budgets and backend APIs remain unchanged.

**Subsequent navigation update:** The separately authorized [M2.1a](m2-owner-workspace.md#try-owner-project-chat) makes **Project chat** the Owner landing page, with **New chat**, **Project resources** and **Project runs**. The former management destinations remain under **Shared projects**, **Access requests**, **Collaborator chats** and **Activity**. The table and checks below preserve the earlier M1 refresh checkpoint; they are not the current Owner default.

## Design direction

Keep one primary task visible at a time. A stable sidebar separates conversation, evidence, verification and administration. Warm neutral surfaces, restrained green accents, consistent type and generous spacing replace the previous dense stack of controls. Detailed identities and runtime metadata remain available on demand.

The information layout draws on two official product descriptions consulted on 2026-09-17:

- [Claude artifacts](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them) separate substantial content from the conversation. Prism uses a side panel for numbered evidence and citations without leaving the current task.
- [Perplexity Projects](https://www.perplexity.ai/en-GB/hub/products/projects) organize work around project context and files. Prism separates the shared project, its sources and its conversations.

These are interface references, not claims that Prism implements those products' capabilities or security models. No external fonts, scripts, UI service or new dependency was added.

## Current navigation

| Area | Default view | Other destinations |
| --- | --- | --- |
| Owner | **Shared projects**, with version status and an open action | **New share**, **Access requests**, **Conversations**, **Activity**, **Model & privacy** |
| Reviewer | **Conversation**, with suggested questions and a fixed composer | **Sources**, **Verification**, **Access & details**, model/privacy details |

**New share** follows selection, frozen-version review and sharing. Content and the fixed executable can be inspected before approval. Model settings, activity and requests no longer occupy the creation page. Revocation requires an explicit confirmation and explains that delivered content cannot be recalled.

**Conversation** keeps its draft and history when switching destinations in the same mounted session. Suggestion buttons fill the composer; they do not send a message or start execution. Enter sends and Shift+Enter inserts a newline; IME composition does not submit. Numbered citations open the evidence panel. Claim/provenance details are expandable, while limitations remain visible. Model availability, finite allowance and provider/owner visibility remain discoverable; an unconfigured model never produces a simulated answer.

**Sources** provides scoped search and file previews. **Verification** retains the actual fixed evaluator and its bounded seed, recorded baseline, new results and execution evidence. **Access & details** explains permitted and excluded work and accepts a pending request. M1 still has no grant-approval or denial workflow; the request grants nothing.

The sidebar becomes a drawer on narrow screens. Evidence and confirmation panels use a native modal dialog with an Escape close action. This was checked at desktop and 390px width; it is not a full accessibility or cross-browser certification.

## Try the refreshed interface

Use the existing running demo, or build and start it as described in the [M1 guide](m1-local-sharing.md#run-the-current-increment). Reload an open page after rebuilding assets. The non-editable Python package must also include the rebuilt static files; `uv sync --no-editable` refreshes that local package. Preserve the explicitly authorized model allowance when restarting a configured demo.

1. Open the authenticated Owner workspace. Inspect **Shared projects**, then choose **New share**. Select the four synthetic evidence files and leave the private note excluded.
2. Choose **Review selected content**, open a file and **Inspect executable**, then approve the exact reviewed version. Open Reviewer and start a fresh session.
3. Select **Understand the finding**. Confirm it fills the composer without sending. Switch to **Sources** and search for `mean`; open `baseline.json` in the side panel. Return to **Conversation** and check that the draft remains.
4. Open **Verification**. Seed `1001` must disable execution. With the documented local runtime available, seed `23` produces a real run and separate baseline/result values. This manual path needs no model call.
5. In **Access & details**, submit a synthetic additional-access request. In Owner, open **Access requests** and refresh; it remains pending and grants no rights.
6. For a disposable test share, choose **Revoke access**. **Keep access** cancels. Confirming revocation blocks a subsequent new evidence request. Existing on-screen content may remain because it was already delivered.

## Verification record

- `npm run build`: passed with the existing locally pinned frontend dependencies.
- `uv run --no-editable python -m unittest discover -s tests -p test_sharing.py -v`: **13 passed**. These check snapshot and API boundaries with temporary files/SQLite and an in-process client; they do not exercise a live model.
- Browser acceptance used a separate synthetic demo on loopback port 8766 with no provider credential. Selection, frozen preview, executable inspection, approval, fresh session, scoped search, draft preservation, unavailable-model state, request submission/owner visibility, confirmation cancellation, revocation and denied subsequent reads passed.
- The same browser flow completed one real local container run with seed 23: mean difference `0.0225`, interval approximately `0.0025` to `0.04125`, exit 0 and confirmed container removal. Invalid seed `1001` was disabled in the UI; API denial coverage remains in the focused suite.
- Desktop and 390px layouts were inspected; the narrow conversation page had no horizontal overflow. Sidebar navigation and Escape dismissal of the evidence dialog worked. No browser console errors were reported in the inspected test reviewer tab.
- No live model request, cloud start, public deployment or budget increase was performed. The main demo retained its four existing versions and the same model dispatch/reservation totals. The isolated UI test state is not a way to extend the main model allowance.

Historical [live-model validation](validation/m1-live-model.json), including failed turns, remains the evidence for the model route. This refresh does not establish improved agent reasoning, semantic citation accuracy, remote authentication or pilot isolation. Owner acceptance of the refreshed interface is the next gate; M2 remains unapproved.
