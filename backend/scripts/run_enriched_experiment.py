"""Build the enriched country-month dataset, compare model variants, write a report.

    python scripts/run_enriched_experiment.py --out /path/to/output_dir

Outputs: results.json, coverage.csv, enriched_sample.csv (same 30 rows as the
original sample: UKR/SDN/COL/FRA/MEX, Jan–Jun 2025, with every new column).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from app.core.db import session_scope
from app.ml.enrichment import build_enriched, coverage, run_comparison


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with session_scope() as s:
        results = run_comparison(s)
        df = build_enriched(s, include_news=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    coverage(df).to_csv(out / "coverage.csv", index=False)
    sample = df[df.iso3.isin(["UKR", "SDN", "COL", "FRA", "MEX"]) & (df.month >= "2025-01-01") & (df.month <= "2025-06-01")].copy()
    sample["month"] = sample["month"].dt.strftime("%Y-%m")
    sample = sample.rename(columns={"state_based_event": "LABEL_fighting_this_month"}).round(4)
    sample.to_csv(out / "enriched_sample.csv", index=False)
    pd.set_option("display.width", 200)
    for name, v in results["variants"].items():
        on, al = v["onset"], v["all"]
        print(f"{name:18s} ALL pr_auc={al['pr_auc']:.3f} brier={al['brier']:.4f} | "
              f"ONSET pr_auc={on.get('pr_auc', float('nan')):.3f} ci95={tuple(round(x, 3) for x in on.get('pr_auc_ci95', (0, 0)))} "
              f"roc={on.get('roc_auc', float('nan')):.3f} brier={on['brier']:.4f} | "
              f"top20 caught={v['onset_top20_monthly']['share_of_all_onsets_caught']:.2f}")
    print("top features (full model):", results["feature_importance_D"][:12])


if __name__ == "__main__":
    main()
