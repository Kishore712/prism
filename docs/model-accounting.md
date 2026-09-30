# Model Accounting and the Local Demo Cost Policy

**Date:** 2026-09-30. **Status:** Implemented locally and activated on the existing loopback demo, after the owner's explicit instruction to remove its application cost cap. No private-server release or provider-account billing setting was changed. See [validation](validation/model-cost-policy.json).

The historical application ledger reserves five cents before each actual provider dispatch. That is a conservative bookkeeping value, not a token-based cost calculation or provider invoice. Existing reservations and recorded token/request usage are preserved. Removing a cap must not reset the ledger, alter previous failures or imply a refund.

`--no-model-budget-limit` explicitly disables the aggregate application reservation ceiling. Owner and collaborator adapters share that setting and the existing ledger. The UI displays **No application cost cap**, while keeping recorded reservations separate. No ceiling or remaining balance is fabricated from the null value.

This is a trusted startup choice, not an API field, model tool or collaborator control. The CLI rejects combining it with `--model-budget-cents`. Existing finite defaults and explicit `--model-budget-cents 0` are unchanged; zero still disables calls. Without an explicitly configured purpose-specific key, uncapped accounting cannot enable inference. Revocation, owner disclosure consent, bounded model requests per turn, tool/time/output limits and task-runtime isolation remain enforced. Provider-side account and rate limits remain provider controls.

The current authorized local launch command is:

```sh
cd /Users/nozomi64/Documents/prism
uv run --no-editable prism demo \
  --data-dir /Users/nozomi64/Documents/prism/.prism-demo \
  --port 8768 \
  --no-model-budget-limit \
  --allow-openai \
  --openai-key-file /Users/nozomi64/Documents/prism/.prism-demo/prism-openai.key \
  --project /Users/nozomi64/Documents/prism/examples/agent-workspace/.prism-project.json
```

One process at a time owns this data directory. The application prints ephemeral local owner/reviewer links on startup; do not store their tokens in the repository. Questions, selected evidence, the scoped session history and authorized tool results go to the already configured OpenAI route. The key is not passed to a computation guest.

Validation passed 482 backend tests, including four new accounting/operator checks; targeted Ruff/format and frontend Prettier/build passed. Focused checks cover ledger preservation/restart, shared owner/collaborator accounting, revoked access, required owner consent, absent credentials and incompatible startup options. They do not contact a real provider. The local route was activated with the existing configured key; a native Chrome owner view displayed the uncapped policy. Actual provider billing was not read, and no token-price estimate is reported as billed cost.

The later [U3 repair record](validation/agent-workspace-followups-repair.json) contains actual model usage under this policy: document follow-up passed; clarification still created an unnecessary permission request. An expired original local session was not extended or bypassed; testing used a fresh session of the same approved synthetic version, scripted baseline copies from an already saved owner artifact and a real baseline check. This is not a second-identity or reference-Linux acceptance claim.
