"""Track record: did the stored outlooks come true?

Every monthly run keeps its predictions (``ml_predictions``, one window per run). Once verified deaths (UCDP) cover a
window's last month, the crisis index of that window is known, so each prediction can be checked with the same rule
the models were trained on: "worse" came true when the window's level is above the level the site showed when the
prediction was made (``current_tier``); "better" when it is below.

The result (per model: windows checked, hit rates, ROC-AUC, Brier) is stored in ``app_settings.ml_track_record`` and
shown on the admin page. It is the honest measure of how the models do on data they never saw, and it is what the
monthly retraining reports next to its own test numbers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.logging import get_logger
from app.ml.models import MlPrediction

log = get_logger(__name__)

RANK = {"low": 0, "elevated": 1, "high": 2, "critical": 3}
SETTING_KEY = "ml_track_record"


def actual_levels(session: Session) -> pd.DataFrame:
    """Per country and month m: the index level over the next 3 and 6 months starting at m (NaN while UCDP does not
    yet cover the whole window)."""
    from app.ml import crisis_index as CI
    from app.ml.enrichment import ucdp_history

    cm, _ = ucdp_history(session)
    hist = CI.historical(session, cm)
    cuts = CI.cuts_for(session)
    out = hist[["iso3", "month"]].copy()
    for h in (3, 6):
        out[f"level_{h}m"] = hist[f"idx_score_next_{h}m"].map(
            lambda v: np.nan if pd.isna(v) else RANK[CI.level_of(v, cuts)])
    return out


def score(preds: pd.DataFrame, threshold: float, kind: str) -> dict:
    """preds: columns probability, now (level rank when predicted), actual (level rank over the window)."""
    from sklearn.metrics import brier_score_loss, roc_auc_score

    y = ((preds["actual"] > preds["now"]) if kind == "worse" else (preds["actual"] < preds["now"])).astype(int)
    p = preds["probability"].to_numpy(dtype=float)
    said = p >= threshold
    out = {"checked": int(len(y)), "happened": int(y.sum()),
           "said": int(said.sum()), "said_and_happened": int(y[said].sum()),
           "hit_rate": round(float(y[said].mean()), 3) if said.any() else None,       # of the "could get …" calls
           "caught": round(float(y[said].sum() / y.sum()), 3) if y.sum() else None,   # of what really happened
           "base_rate": round(float(y.mean()), 3) if len(y) else None,
           "brier": round(float(brier_score_loss(y, p)), 4) if len(y) else None}
    if 0 < y.sum() < len(y):
        out["roc_auc"] = round(float(roc_auc_score(y, p)), 3)
    return out


def compute(session: Session) -> dict:
    from app.ml.predictions import CALM_MODELS, MODELS, stored_model_info

    rows = session.execute(select(MlPrediction.model_name, MlPrediction.model_version, MlPrediction.iso3,
                                  MlPrediction.window_start, MlPrediction.current_tier, MlPrediction.probability)
                           .where(MlPrediction.status == "predicted", MlPrediction.probability.is_not(None))).all()
    result: dict = {"computed_at": utcnow().isoformat(), "models": {}}
    if not rows:
        return result
    df = pd.DataFrame(rows, columns=["model", "version", "iso3", "month", "now", "probability"])
    df["month"] = pd.to_datetime(df["month"])
    df["now"] = df["now"].map(RANK)
    # one prediction per model, country and window: the newest stored version
    df = df.sort_values("version").groupby(["model", "iso3", "month"], as_index=False).last()
    actual = actual_levels(session)
    for kind, models in (("worse", MODELS), ("better", CALM_MODELS)):
        for h, name in models.items():
            d = df[df["model"] == name].merge(actual[["iso3", "month", f"level_{h}m"]], on=["iso3", "month"], how="left")
            d = d.rename(columns={f"level_{h}m": "actual"}).dropna(subset=["actual", "now"])
            pending = int(df[df["model"] == name]["month"].nunique() - d["month"].nunique())
            info = stored_model_info(session, name)
            thr = float(info.get("decision_threshold") or (0.15 if kind == "worse" else 0.27))  # as predictions.outlooks
            entry = {"horizon_months": h, "direction": kind, "threshold": thr, "windows_checked": int(d["month"].nunique()),
                     "windows_pending": pending}
            if len(d):
                entry.update(score(d, thr, kind))
                entry["windows"] = [str(m.date()) for m in sorted(d["month"].unique())]
                entry["test_roc_auc"] = (info.get("test") or {}).get("model", {}).get("roc_auc")
            result["models"][name] = entry
    return result


def update(session: Session) -> dict:
    from app.ingestion.models import AppSetting

    res = compute(session)
    row = session.get(AppSetting, SETTING_KEY)
    if row is None:
        row = AppSetting(key=SETTING_KEY)
        session.add(row)
    row.value = res
    row.updated_at = utcnow()
    row.updated_by = "track-record"
    session.flush()
    log.info("track_record_updated", models={k: v.get("windows_checked") for k, v in res["models"].items()})
    return res


def stored(session: Session) -> dict:
    from app.ingestion.models import AppSetting

    row = session.get(AppSetting, SETTING_KEY)
    return row.value if row and isinstance(row.value, dict) else {}
