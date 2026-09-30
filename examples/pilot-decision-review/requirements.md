# Synthetic pilot analysis requirements

All identifiers and revenue units are synthetic. Nothing authorizes publication, rollout or deployment. Approval stays pending.

Remove exact duplicate rows; reject missing user IDs with a reason. Conflicting rows for one user must block analysis. Use all eligible users as denominators. Compare conversion and revenue per user overall and within each segment, then standardize with analysis-plan.json weights. Distinguish percentage points from relative change and composition effects from causal evidence.

For the later complete analysis, sample with replacement within each variant/segment retaining stratum sizes, in order A/new, A/returning, B/new, B/returning. Sort each stratum by user_id; choose each sampled index with rng.randrange(len(stratum)). Use random.Random(17), 2,000 replicates, conversion before revenue (the same sampled users for both). For the 95% percentile interval sort all differences and linearly interpolate at index (n-1)*p for p=.025 and .975. Report assumptions and finite-resample limitations.

Write only results/metrics.json and results/data-quality.json as deliverable numerical outputs. The report needs human review. Runtime completion alone does not verify methodology. The supplied script is intentionally an incomplete raw comparison for a later agent repair task.
