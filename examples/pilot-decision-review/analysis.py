"""Incomplete raw comparison; not an accepted pilot analysis."""
import csv
import json
from pathlib import Path

rows = list(csv.DictReader(Path("data/pilot.csv").open()))
metrics = {"method": "raw rows; cleaning and standardization pending", "variants": {}}
for variant in ("A", "B"):
    sample = [r for r in rows if r["variant"] == variant]
    n = len(sample)
    conversions = sum(int(r["converted"]) for r in sample)
    revenue = sum(float(r["revenue"]) for r in sample)
    metrics["variants"][variant] = {"rows": n, "conversions": conversions,
        "conversion_rate": conversions / n, "revenue": revenue, "revenue_per_row": revenue / n}
Path("results/metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
Path("results/data-quality.json").write_text(json.dumps({"raw_rows": len(rows), "cleaning_performed": False}, indent=2) + "\n")
