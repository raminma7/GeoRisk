"""Baseline ML experiment with reproducible, time-respecting validation.

Task: predict ``state_based_event_next_1m`` (UCDP) for each country-month
from lagged UCDP history (only months strictly before the target window).

* walk-forward validation: for each test year Y, train on all months < Y,
  calibrate on the last training year (isotonic), test on Y
* class imbalance: ``scale_pos_weight`` from the training prevalence
* baselines reported alongside: persistence (event last month) and the
  training base rate
* reproducibility: fixed seed, versioned dataset (checksum) and model rows

Point-in-time caveat (recorded in the dataset definition): historical UCDP
features use the final coding of the imported release ("research mode").
Live snapshots in ``feature_snapshots`` respect release availability.

Model scores are experimental research outputs — never displayed as war
probabilities.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingestion.connectors.ucdp import latest_version
from app.ml.labels import LABEL_VERSION, compute_labels, ucdp_country_months
from app.ml.models import ModelEvaluation, ModelVersion, TrainingDataset

log = get_logger(__name__)
SEED = 20261119
FEATURES = [
    "sb_events_l1", "sb_events_l3", "sb_events_l6", "sb_events_l12", "sb_deaths_l1", "sb_deaths_l3", "sb_deaths_l12",
    "ns_events_l3", "os_events_l3", "interstate_l12", "months_since_sb", "sb_active_months_l12",
]
TARGET = "state_based_event"


def build_frame(cm: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """Country-month frame: lagged features (strictly before month t) + forward label [t, t+h)."""
    frames = []
    for iso, g in cm.sort_values("month").groupby("iso3"):
        g = g.reset_index(drop=True)
        f = pd.DataFrame({"iso3": iso, "month": g["month"]})
        for col, name in (("sb_events", "sb_events"), ("sb_deaths", "sb_deaths")):
            for lag in (1, 3, 6, 12):
                f[f"{name}_l{lag}"] = g[col].shift(1).rolling(lag, min_periods=1).sum()
        f["ns_events_l3"] = g["ns_events"].shift(1).rolling(3, min_periods=1).sum()
        f["os_events_l3"] = g["os_events"].shift(1).rolling(3, min_periods=1).sum()
        f["interstate_l12"] = g["interstate_events"].shift(1).rolling(12, min_periods=1).sum()
        active = (g["sb_events"] > 0).astype(int)
        f["sb_active_months_l12"] = active.shift(1).rolling(12, min_periods=1).sum()
        last = g["month"].where(active == 1).ffill().shift(1)
        f["months_since_sb"] = ((g["month"].dt.year - last.dt.year) * 12 + (g["month"].dt.month - last.dt.month)).fillna(240)
        frames.append(f)
    feats = pd.concat(frames, ignore_index=True)
    labels = compute_labels(cm, horizon)[["iso3", "month", TARGET]]
    df = feats.merge(labels, on=["iso3", "month"], how="inner").dropna(subset=[TARGET])
    df = df.iloc[12:] if len(df) > 12 else df
    return df.dropna(subset=["sb_events_l1"]).reset_index(drop=True)


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    out = {"n": int(len(y)), "positives": int(y.sum()), "base_rate": float(y.mean()) if len(y) else None,
           "brier": float(brier_score_loss(y, p)), "log_loss": float(log_loss(y, p, labels=[0, 1]))}
    if 0 < y.sum() < len(y):
        out["roc_auc"] = float(roc_auc_score(y, p))
        out["pr_auc"] = float(average_precision_score(y, p))
    bins = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(p, bins) - 1, 0, 9)
    out["calibration"] = [{"bin": f"{bins[i]:.1f}-{bins[i+1]:.1f}", "n": int((idx == i).sum()),
                           "mean_pred": float(p[idx == i].mean()), "observed": float(y[idx == i].mean())}
                          for i in range(10) if (idx == i).sum()]
    return out


def _lgbm(pos_weight: float):
    import lightgbm as lgb

    return lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=30,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, scale_pos_weight=pos_weight,
                              random_state=SEED, deterministic=True, force_row_wise=True, verbose=-1)


def walk_forward(df: pd.DataFrame, test_years: list[int]) -> list[dict]:
    results = []
    for year in test_years:
        train = df[df["month"].dt.year < year]
        test = df[df["month"].dt.year == year]
        if len(test) == 0 or train[TARGET].nunique() < 2:
            continue
        calib = train[train["month"].dt.year == year - 1]
        fit = train[train["month"].dt.year < year - 1]
        if len(fit) == 0 or fit[TARGET].nunique() < 2 or len(calib) == 0:
            fit, calib = train, None
        pos = fit[TARGET].mean()
        model = _lgbm(pos_weight=(1 - pos) / max(pos, 1e-6))
        model.fit(fit[FEATURES], fit[TARGET].astype(int))
        raw = model.predict_proba(test[FEATURES])[:, 1]
        iso = None
        if calib is not None and calib[TARGET].nunique() == 2:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
            iso.fit(model.predict_proba(calib[FEATURES])[:, 1], calib[TARGET].astype(int))
            p = iso.predict(raw)
        else:
            p = raw
        logit = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED)
        logit.fit(np.log1p(train[FEATURES].clip(lower=0)), train[TARGET].astype(int))
        y = test[TARGET].astype(int).to_numpy()
        results.append({
            "test_year": year, "train_end": str(train["month"].max().date()), "train_start": str(train["month"].min().date()),
            "test_start": str(test["month"].min().date()), "test_end": str(test["month"].max().date()),
            "lightgbm_calibrated": _metrics(y, p),
            "lightgbm_raw": _metrics(y, raw),
            "logistic_regression": _metrics(y, logit.predict_proba(np.log1p(test[FEATURES].clip(lower=0)))[:, 1]),
            "persistence": _metrics(y, (test["sb_events_l1"] > 0).astype(float).to_numpy() * 0.9 + 0.05),
            "base_rate": _metrics(y, np.full(len(y), train[TARGET].mean())),
            "calibrated": iso is not None, "_model": model,
        })
    return results


def train_ucdp_baseline(session: Session, label_months: int = 1, n_test_years: int = 4) -> dict:
    dv = latest_version(session)
    if dv is None:
        return {"status": "no_ucdp_dataset", "hint": "run `python -m app.cli import-ucdp` first"}
    cm = ucdp_country_months(session, dv.id)
    if cm.empty:
        return {"status": "no_data"}
    df = build_frame(cm, label_months)
    years = sorted(df["month"].dt.year.unique())
    test_years = [y for y in years[-n_test_years:] if y > years[0] + 2]
    settings = get_settings()

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    checksum = hashlib.sha256(buf.getvalue()).hexdigest()
    version = f"{dv.version}-h{label_months}-{checksum[:10]}"
    ds = session.scalar(select(TrainingDataset).where(TrainingDataset.name == "ucdp_country_month",
                                                      TrainingDataset.version == version))
    if ds is None:
        path = Path(settings.model_dir) / "datasets" / f"ucdp_country_month_{version}.parquet"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(buf.getvalue())
            file_path = str(path)
        except OSError:
            file_path = None
        ds = TrainingDataset(
            name="ucdp_country_month", version=version, row_count=len(df), file_path=file_path, file_format="parquet",
            checksum=checksum,
            definition={"entity": "country", "frequency": "month", "features": FEATURES, "target": f"{TARGET}_next_{label_months}m",
                        "label_version": LABEL_VERSION, "ucdp_version": dv.version, "pit_mode": "research_final_release",
                        "period": [str(df["month"].min().date()), str(df["month"].max().date())]},
            licensing={"sources": ["ucdp_ged"], "license": "CC BY 4.0", "citation": dv.citation},
        )
        session.add(ds)
        session.flush()
    results = walk_forward(df, test_years)
    if not results:
        return {"status": "insufficient_data", "rows": len(df)}
    mv = ModelVersion(name="ucdp_state_based_next_1m_lgbm", version=f"{version}-s{SEED}", algorithm="lightgbm+isotonic",
                      params=_lgbm(1.0).get_params(), training_dataset_id=ds.id, random_seed=SEED,
                      status="experimental", notes="Baseline research model. Not validated for operational use.")
    existing = session.scalar(select(ModelVersion).where(ModelVersion.name == mv.name, ModelVersion.version == mv.version))
    if existing:
        session.delete(existing)
        session.flush()
    session.add(mv)
    session.flush()
    try:
        import joblib

        art = Path(settings.model_dir) / f"{mv.name}_{mv.version}.joblib"
        art.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(results[-1]["_model"], art)
        mv.artifact_path = str(art)
    except OSError:
        pass
    summary = []
    for i, r in enumerate(results):
        session.add(ModelEvaluation(
            model_version_id=mv.id, split_kind="walk_forward", fold=i,
            train_start=pd.Timestamp(r["train_start"]).date(), train_end=pd.Timestamp(r["train_end"]).date(),
            test_start=pd.Timestamp(r["test_start"]).date(), test_end=pd.Timestamp(r["test_end"]).date(),
            metrics=r["lightgbm_calibrated"],
            baseline_metrics={k: r[k] for k in ("lightgbm_raw", "logistic_regression", "persistence", "base_rate")},
            notes="Out-of-time test year; isotonic calibration fitted on the final training year."
                  if r["calibrated"] else "Out-of-time test year; uncalibrated.",
        ))
        summary.append({"test_year": r["test_year"],
                        **{k: {m: r[k].get(m) for m in ("roc_auc", "pr_auc", "brier")}
                           for k in ("lightgbm_calibrated", "logistic_regression", "persistence", "base_rate")}})
    session.flush()
    os.environ.setdefault("GEORISK_LAST_MODEL", str(mv.id))
    return {"status": "ok", "model_version_id": mv.id, "dataset": ds.version, "rows": len(df), "folds": summary,
            "note": "Experimental research baseline; scores are not war probabilities."}
