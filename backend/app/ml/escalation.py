"""Crisis escalation model: "will this country's crisis get worse in the next 3 (or 6) months?"

Aligned with the site's severity: a country's tier is set by verified deaths from all organised violence (UCDP:
state-based, non-state and one-sided) over a year — Low < 100, Moderate 100–999, High 1,000–9,999, Critical
10,000+. The label is 1 when the deaths of the next ``h`` months, scaled to a year, put the country at least one tier
above its tier over the previous 12 months. Peaceful countries (Low) becoming Moderate or worse = a new crisis;
countries already in crisis moving up = escalation. Countries already Critical cannot rise and are excluded.

Features are the onset model's factors (history, neighbours, politics, economy, news dynamics) plus the site's own
signals: deaths from all violence types, the current tier, the recent trend of deaths and the share of violent
news compared with the country's normal, corrected for the worldwide change. Everything is lagged so that row t only
sees data published before month t.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ml.enrichment import GROUPS, SEED, build_enriched

log = get_logger(__name__)

HORIZONS = (3, 6)
TIER_CUTS = (100, 1_000, 10_000)          # deaths per year
TIER_NAMES = ["low", "elevated", "high", "critical"]
LABEL_DEFINITION = ("Deaths from organised violence (UCDP, all types) in the next {h} months, scaled to a year, put "
                    "the country at least one severity tier above its tier over the previous 12 months "
                    "(tiers: <100, 100-999, 1,000-9,999, 10,000+ deaths per year). Countries already at the top "
                    "tier are excluded.")

SELECTED_DYNAMICS = ["news_violent_share_chg_6m", "news_threat_share_chg_6m", "news_avg_tone_chg_3m",
                     "news_avg_goldstein_chg_3m", "news_coverage_change_3m", "news_violent_share", "nb_sb_events_l1_chg_6m",
                     "wdi_log_gdp_per_capita_chg_12m", "vdem_liberal_democracy_chg_12m", "epr_excluded_pop_share_chg_12m"]
SITE_FEATURES = ["all_deaths_l1", "all_deaths_l3", "all_deaths_l12", "tier_now", "deaths_trend_3v9",
                 "ns_events_l12", "os_events_l12", "news_violent_vs_normal", "news_protest_share",
                 "news_diplomatic_tension_share"]


def features() -> list[str]:
    base = (GROUPS["ucdp_history"] + GROUPS["neighbours_region"] + GROUPS["politics"] + GROUPS["economy"]
            + SELECTED_DYNAMICS + SITE_FEATURES)
    return list(dict.fromkeys(base))  # keep order, drop duplicates


def tier_of(annual_deaths: pd.Series | np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(TIER_CUTS), np.asarray(annual_deaths, dtype=float), side="right")


# ------------------------------------------------------------------ features
def site_features(cm: pd.DataFrame, news: pd.DataFrame | None) -> pd.DataFrame:
    """Per country-month, using only months before t."""
    rows = []
    for iso, g in cm.sort_values("month").groupby("iso3"):
        g = g.reset_index(drop=True)
        past = g["all_deaths"].shift(1)
        f = pd.DataFrame({"iso3": iso, "month": g["month"]})
        for lag in (1, 3, 12):
            f[f"all_deaths_l{lag}"] = past.rolling(lag, min_periods=1).sum()
        f["tier_now"] = tier_of(f["all_deaths_l12"].fillna(0))
        prior9 = (f["all_deaths_l12"] - f["all_deaths_l3"]).clip(lower=0)
        f["deaths_trend_3v9"] = (f["all_deaths_l3"] / 3 + 1) / (prior9 / 9 + 1)   # >1: deaths rising lately
        f["ns_events_l12"] = g["ns_events"].shift(1).rolling(12, min_periods=1).sum()
        f["os_events_l12"] = g["os_events"].shift(1).rolling(12, min_periods=1).sum()
        rows.append(f)
    out = pd.concat(rows, ignore_index=True)
    if news is None or news.empty:
        return out.assign(news_violent_vs_normal=np.nan)
    n = news.sort_values(["iso3", "month"]).copy()
    n["share"] = n["violent"] / n["events"].replace(0, np.nan)
    world = n.groupby("month").agg(v=("violent", "sum"), e=("events", "sum"))
    world["wshare"] = world["v"] / world["e"]
    world["wnorm"] = world["wshare"].shift(1).rolling(12, min_periods=6).mean()
    n = n.merge(world[["wshare", "wnorm"]], left_on="month", right_index=True, how="left")
    n["norm"] = n.groupby("iso3")["share"].transform(lambda s: s.shift(1).rolling(12, min_periods=6).mean())
    # this month's violent share vs the country's normal, divided by the same ratio for the whole world
    n["news_violent_vs_normal"] = (n["share"] / n["norm"]) / (n["wshare"] / n["wnorm"])
    n.loc[n["events"] < 300, "news_violent_vs_normal"] = np.nan        # too little news to judge
    n["news_violent_vs_normal"] = n.groupby("iso3")["news_violent_vs_normal"].shift(1)  # row t sees t-1
    return out.merge(n[["iso3", "month", "news_violent_vs_normal"]], on=["iso3", "month"], how="left")


def escalation_labels(cm: pd.DataFrame, h: int) -> pd.DataFrame:
    rows = []
    for iso, g in cm.sort_values("month").groupby("iso3"):
        g = g.reset_index(drop=True)
        prev12 = g["all_deaths"].shift(1).rolling(12, min_periods=12).sum()
        nxt = g["all_deaths"][::-1].rolling(h, min_periods=h).sum()[::-1]   # months t..t+h-1
        now_t, next_t = tier_of(prev12.fillna(0)), tier_of((nxt * 12 / h).fillna(0))
        ok = prev12.notna() & nxt.notna()
        rows.append(pd.DataFrame({
            "iso3": iso, "month": g["month"],
            f"can_rise_{h}m": ((now_t < len(TIER_CUTS)) & prev12.notna()).astype(int),
            f"deaths_next_{h}m": nxt,
            f"worse_{h}m": np.where(ok & (now_t < len(TIER_CUTS)), (next_t > now_t).astype(float), np.nan),
        }))
    return pd.concat(rows, ignore_index=True)


def build_frame(session: Session, forecast: bool = False) -> pd.DataFrame:
    from app.ml.enrichment import ucdp_history
    from app.ml.news_history import monthly_frame

    df = build_enriched(session, include_news=True, forecast=forecast)
    cm, _ = ucdp_history(session)
    if forecast:
        nxt = cm["month"].max() + pd.DateOffset(months=1)
        cm = pd.concat([cm, pd.DataFrame({"iso3": sorted(cm["iso3"].unique()), "month": nxt})], ignore_index=True)
    news = monthly_frame(session)
    out = df.merge(site_features(cm, news), on=["iso3", "month"], how="left")
    for h in HORIZONS:
        out = out.merge(escalation_labels(cm, h), on=["iso3", "month"], how="left")
    out.attrs = df.attrs
    return out


# ------------------------------------------------------------------ model
def classifier():
    """Regularised gradient-boosted trees (more positives than the old onset label, so slightly deeper trees)."""
    import lightgbm as lgb

    return lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, max_depth=3, num_leaves=8, min_child_samples=100,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0,
                              random_state=SEED, verbose=-1)


def walk_forward(df: pd.DataFrame, feats: list[str], h: int, years: list[int]) -> pd.DataFrame:
    """Out-of-time predictions: each test year is scored by a model trained only on rows whose whole label window
    ended before that year (no outcome from the test year leaks into training)."""
    label, risk = f"worse_{h}m", f"can_rise_{h}m"
    data = df[(df[risk] == 1) & df[label].notna()]
    out = []
    for year in years:
        cutoff = pd.Timestamp(year=year, month=1, day=1) - pd.DateOffset(months=h - 1)
        train, test = data[data["month"] < cutoff], data[data["month"].dt.year == year]
        if test.empty:
            continue
        m = classifier().fit(train[feats], train[label].astype(int))
        out.append(test[["iso3", "month", label, "tier_now", "deaths_trend_3v9", "news_violent_vs_normal"]]
                   .assign(raw=m.predict_proba(test[feats])[:, 1], year=year))
    return pd.concat(out, ignore_index=True)


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score

    if not 0 < y.sum() < len(y):
        return {"n": len(y), "positives": int(y.sum())}
    return {"n": len(y), "positives": int(y.sum()), "base_rate": round(float(y.mean()), 4),
            "roc_auc": round(float(roc_auc_score(y, p)), 3), "pr_auc": round(float(average_precision_score(y, p)), 3)}


def top_k_caught(pred: pd.DataFrame, label: str, score: str, k: int = 20) -> float:
    """Share of all 'got worse' cases that were among the k highest-scored countries in their month."""
    r = pred.sort_values(score, ascending=False).groupby("month").head(k)
    return round(float(r[label].sum() / max(pred[label].sum(), 1)), 3)


def report(pred: pd.DataFrame, h: int) -> dict:
    label = f"worse_{h}m"
    y = pred[label].astype(int).to_numpy()
    scores = {
        "model": pred["raw"].to_numpy(),
        # simple rules a reader could apply by hand, for comparison
        "rule_deaths_trend": pred["deaths_trend_3v9"].fillna(1).to_numpy(),
        "rule_violent_news": pred["news_violent_vs_normal"].fillna(1).to_numpy(),
    }
    out: dict = {}
    for name, s in scores.items():
        tmp = pred.assign(_s=s)
        out[name] = {**_metrics(y, s), "top20_caught": top_k_caught(tmp, label, "_s")}
    by_tier = {}
    for t, name in enumerate(TIER_NAMES[:-1]):
        sub = pred[pred["tier_now"] == t]
        by_tier[name] = _metrics(sub[label].astype(int).to_numpy(), sub["raw"].to_numpy())
    out["model_by_current_tier"] = by_tier
    return out


def importance(model, feats: list[str], top: int = 20) -> list[tuple[str, float]]:
    gain = model.booster_.feature_importance(importance_type="gain")
    tot = gain.sum() or 1
    return sorted(((f, round(float(g / tot), 4)) for f, g in zip(feats, gain)), key=lambda x: -x[1])[:top]


def train_and_export(session: Session, test_years: list[int] | None = None,
                     df: pd.DataFrame | None = None) -> dict:
    from sklearn.linear_model import LogisticRegression

    settings = get_settings()
    test_years = test_years or list(range(2013, 2026))
    df = df if df is not None else build_frame(session)
    prov = df.attrs.get("provenance", {})
    feats = features()
    results = {}
    for h in HORIZONS:
        label, risk = f"worse_{h}m", f"can_rise_{h}m"
        pred = walk_forward(df, feats, h, test_years)
        rep = report(pred, h)
        # Platt calibration on the out-of-time raw scores
        raw = np.clip(pred["raw"].to_numpy(), 1e-6, 1 - 1e-6)
        platt = LogisticRegression(C=1e6, max_iter=1000).fit(np.log(raw / (1 - raw)).reshape(-1, 1),
                                                            pred[label].astype(int))
        grid = np.unique(np.concatenate([np.linspace(0, 1, 401), np.quantile(raw, np.linspace(0, 1, 201))]))
        gl = np.log(np.clip(grid, 1e-6, 1 - 1e-6) / (1 - np.clip(grid, 1e-6, 1 - 1e-6))).reshape(-1, 1)
        cal_y = platt.predict_proba(gl)[:, 1]

        data = df[(df[risk] == 1) & df[label].notna()]
        model = classifier().fit(data[feats], data[label].astype(int))
        buf = io.BytesIO()
        data[["iso3", "month", *feats, label]].to_parquet(buf, index=False)
        checksum = hashlib.sha256(buf.getvalue()).hexdigest()
        trained = datetime.now(UTC)
        version = f"v{trained:%Y%m%d}-{checksum[:8]}"
        name = f"crisis_escalation_{h}m"
        pkg = Path(settings.model_dir) / name / version
        pkg.mkdir(parents=True, exist_ok=True)
        model.booster_.save_model(str(pkg / "model.txt"))
        (pkg / "calibrator.json").write_text(json.dumps({"method": "platt_on_logit_raw", "x": grid.tolist(),
                                                         "y": cal_y.tolist()}))
        meta = {
            "name": name, "version": version, "trained_at": trained.isoformat(), "horizon_months": h,
            "features": feats, "label": label, "label_definition": LABEL_DEFINITION.format(h=h),
            "applies_to": "Every country except those already at the top tier (10,000+ deaths a year)",
            "prediction_window": f"the {h} months after the data ends",
            "training_rows": len(data), "training_positives": int(data[label].sum()),
            "base_rate": float(data[label].mean()),
            "algorithm": "lightgbm (depth-3, regularised) + Platt calibration on out-of-time scores",
            "data_checksum": checksum, "provenance": prov, "random_seed": SEED,
            "test": {"years": [test_years[0], test_years[-1]], **rep},
            "top_features": importance(model, feats),
            "disclaimer": "Research estimate based on historical patterns, not a forecast of events.",
        }
        (pkg / "metadata.json").write_text(json.dumps(meta, indent=2, default=str))
        log.info("escalation_model_exported", name=name, version=version, path=str(pkg))
        results[name] = {"version": version, "path": str(pkg), "test": rep, "top_features": meta["top_features"],
                         "training_rows": meta["training_rows"], "training_positives": meta["training_positives"]}
    return results


def calibrate(raw: np.ndarray, cal: dict) -> np.ndarray:
    return np.interp(raw, cal["x"], cal["y"])
