"""Train the "new conflict" (onset) model on clean labels and export a stand-alone model package.

Label (``onset-major-v1``): a country with no UCDP state-based deaths in the previous 24 months records
>= 25 deaths in months t..t+2. Training rows are only those "quiet" country-months.

The exported package (``<MODEL_DIR>/conflict_onset/<version>/``) is everything the separate
``model-service`` needs — it never imports this application:

* ``model.txt``        LightGBM booster (native text format)
* ``calibrator.json``  isotonic mapping raw score -> calibrated probability (fitted on out-of-time
                       walk-forward predictions, so it reflects performance on unseen years)
* ``metadata.json``    ordered feature list, label definition, data provenance, test metrics
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ml.baseline import SEED
from app.ml.enrichment import GROUPS, build_enriched, evaluate

log = get_logger(__name__)
LABEL = "onset_major"
RISK = "onset_risk_major"
LABEL_DEFINITION = ("New conflict: no UCDP state-based deaths in the previous 24 months, then >= 25 deaths "
                    "(UCDP best estimate) within the next 3 months (t..t+2).")
# Selected dynamics (no global prices: they encode time, not place)
SELECTED_DYNAMICS = ["news_violent_share_chg_6m", "news_threat_share_chg_6m", "news_avg_tone_chg_3m",
                     "news_avg_goldstein_chg_3m", "news_coverage_change_3m", "news_violent_share", "nb_sb_events_l1_chg_6m",
                     "wdi_log_gdp_per_capita_chg_12m", "vdem_liberal_democracy_chg_12m", "epr_excluded_pop_share_chg_12m"]


def onset_classifier():
    """Small, heavily regularised gradient-boosted trees. Onsets are rare (~150 labelled months), so shallow
    trees (depth 2), large leaves (>=150 rows) and row/feature subsampling generalise far better than a
    default LightGBM, which memorises the few positives (out-of-time ROC ~0.5 vs ~0.9)."""
    import lightgbm as lgb

    return lgb.LGBMClassifier(n_estimators=200, learning_rate=0.03, num_leaves=4, max_depth=2, min_child_samples=150,
                              subsample=0.7, subsample_freq=1, colsample_bytree=0.6, reg_lambda=5.0, random_state=SEED,
                              verbose=-1, deterministic=True, force_row_wise=True)


def variants() -> dict[str, list[str]]:
    a = list(GROUPS["ucdp_history"])
    b = a + GROUPS["neighbours_region"] + GROUPS["politics"] + GROUPS["economy"]
    return {"A_history_only": a, "B_structural": b, "G_structural_plus_dynamics": b + SELECTED_DYNAMICS}


def onsets_list(session: Session, df: pd.DataFrame) -> pd.DataFrame:
    """Human-readable list of labelled onsets with the UCDP conflicts behind them."""
    on = df[(df[RISK] == 1) & (df[LABEL] == 1)][["iso3", "month", "deaths_next_3m"]].copy()
    names = pd.read_sql(text(
        "SELECT c.iso3, date_trunc('month', u.date_start)::date AS month, string_agg(DISTINCT u.conflict_name, '; ') AS conflicts "
        "FROM ucdp_events u JOIN countries c ON c.id = u.country_id WHERE u.type_of_violence = 1 GROUP BY 1, 2"),
        session.connection())
    names["month"] = pd.to_datetime(names["month"])
    rows = []
    for r in on.itertuples(index=False):
        window = names[(names.iso3 == r.iso3) & (names.month >= r.month) & (names.month < r.month + pd.DateOffset(months=3))]
        rows.append({"iso3": r.iso3, "month": r.month.strftime("%Y-%m"), "deaths_next_3m": int(r.deaths_next_3m),
                     "conflicts": "; ".join(sorted(set("; ".join(window.conflicts).split("; ")) - {""}))[:300]})
    return pd.DataFrame(rows)


def experiment(df: pd.DataFrame, test_years: list[int]) -> dict:
    data = df[(df[RISK] == 1) & df[LABEL].notna()]
    out = {}
    for name, feats in variants().items():
        res, _ = evaluate(data, feats, test_years, target=LABEL, risk_col=RISK, make_model=onset_classifier)
        o, top = res["onset"], res["onset_top20_monthly"]
        out[name] = {"n_features": len(feats), "onsets_tested": o["positives"], "rows_tested": o["n"],
                     "roc_auc": o.get("roc_auc"), "roc_auc_ci95": o.get("roc_auc_ci95"), "pr_auc": o.get("pr_auc"),
                     "pr_auc_ci95": o.get("pr_auc_ci95"), "brier": o["brier"], "base_rate": o["base_rate"],
                     "top20_share_caught": top["share_of_all_onsets_caught"], "top20_precision": top["precision"]}
        log.info("onset_variant", variant=name, roc=o.get("roc_auc"), caught=top["share_of_all_onsets_caught"])
    return out


def train_and_export(session: Session, test_years: list[int] | None = None, variant: str | None = None) -> dict:
    settings = get_settings()
    test_years = test_years or list(range(2013, 2026))
    df = build_enriched(session, include_news=True)
    prov = df.attrs.get("provenance", {})
    data = df[(df[RISK] == 1) & df[LABEL].notna()].copy()
    results = experiment(df, test_years)
    best = variant or max(results, key=lambda k: (results[k]["roc_auc"] or 0))
    feats = variants()[best]

    # Smooth (Platt / logistic) calibrator fitted on out-of-time raw scores of the chosen variant. Isotonic
    # regression produces step plateaus with so few positives, which would give many countries identical risk.
    _, allp = evaluate(data, feats, test_years, target=LABEL, risk_col=RISK, make_model=onset_classifier)
    raw_oot = allp["raw"].to_numpy()
    logit = np.log(np.clip(raw_oot, 1e-6, 1 - 1e-6) / (1 - np.clip(raw_oot, 1e-6, 1 - 1e-6))).reshape(-1, 1)
    platt = LogisticRegression(C=1e6, max_iter=1000).fit(logit, allp["state_based_event"].astype(int).to_numpy())
    grid = np.unique(np.concatenate([np.linspace(0, 1, 401), np.quantile(raw_oot, np.linspace(0, 1, 201))]))
    g = np.log(np.clip(grid, 1e-6, 1 - 1e-6) / (1 - np.clip(grid, 1e-6, 1 - 1e-6))).reshape(-1, 1)
    cal_y = platt.predict_proba(g)[:, 1]

    # final model on every labelled quiet country-month
    pos = data[LABEL].mean()
    model = onset_classifier()
    model.fit(data[feats], data[LABEL].astype(int))

    buf = io.BytesIO()
    data[["iso3", "month", *feats, LABEL]].to_parquet(buf, index=False)
    checksum = hashlib.sha256(buf.getvalue()).hexdigest()
    trained = datetime.now(timezone.utc)
    version = f"v{trained:%Y%m%d}-{checksum[:8]}"
    pkg = Path(settings.model_dir) / "conflict_onset" / version
    pkg.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(pkg / "model.txt"))
    (pkg / "calibrator.json").write_text(json.dumps({"method": "platt_on_logit_raw", "x": grid.tolist(), "y": cal_y.tolist()}))
    meta = {
        "name": "conflict_onset", "version": version, "trained_at": trained.isoformat(), "variant": best,
        "features": feats, "label": LABEL, "label_definition": LABEL_DEFINITION,
        "applies_to": "Countries with no UCDP state-based deaths in the previous 24 months",
        "prediction_window": "the month after the data ends and the following 2 months (t..t+2)",
        "training_rows": int(len(data)), "training_onsets": int(data[LABEL].sum()),
        "base_rate": float(pos), "algorithm": "lightgbm (depth-2, regularised) + Platt calibration (out-of-time)",
        "selection_note": "Model type chosen by comparing 3 configurations on the same test years "
                          "(slightly optimistic estimate).", "data_checksum": checksum, "provenance": prov, "random_seed": SEED,
        "test": {"years": [test_years[0], test_years[-1]], **results[best]}, "all_variants": results,
        "disclaimer": "Experimental research estimate based on historical patterns. Not a probability of war.",
    }
    (pkg / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    log.info("onset_model_exported", version=version, variant=best, path=str(pkg))
    return {"version": version, "path": str(pkg), "variant": best, "results": results, "provenance": prov,
            "training_onsets": meta["training_onsets"]}


def calibrate(raw: np.ndarray, cal: dict) -> np.ndarray:
    return np.interp(raw, cal["x"], cal["y"])
