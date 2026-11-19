"""Enriched country-month training dataset and honest model comparison.

Every row = (country, month t). Label = UCDP state-based fighting in month t (and
the *onset* subset: no fighting in the previous 12 months). Every feature uses only
information that existed before month t:

* UCDP history          – months < t (same as the baseline)
* neighbours / region   – neighbours' & sub-region's UCDP history, months < t
* V-Dem regime          – year Y-1 from April of Y (V-Dem releases ~March), else Y-2
* World Bank WDI        – year Y-1 from July of Y (WDI releases ~mid-year), else Y-2
* EPR ethnic exclusion  – year Y-1 (dataset ends 2021 -> later years are null)
* commodity prices      – month t-1 (FRED/IMF monthly series)
* news (GDELT 1.0)      – month t-1 and t-3..t-1, as shares of world coverage

Missing values stay NaN (LightGBM handles them natively); nothing is imputed as 0
except UCDP counts, where "no recorded event" genuinely means zero.

Research caveat: WDI/V-Dem/EPR/UCDP are current vintages (revised), so this is
``pit_mode=research_final_release`` — not a strict real-time reconstruction.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingestion.connectors.ucdp import latest_version
from app.ml.baseline import FEATURES as UCDP_FEATURES
from app.ml.baseline import SEED, _lgbm, _metrics, build_frame
from app.ml.labels import ucdp_country_months
from app.ml.models import ModelEvaluation, ModelVersion, TrainingDataset
from app.registry.models import Country

log = get_logger(__name__)
FACTOR_DIR = Path("/var/tmp/georisk/factors")
TARGET = "state_based_event"
WDI = {
    "NY.GDP.PCAP.KD": "wdi_gdp_per_capita_const_usd",
    "NY.GDP.MKTP.KD.ZG": "wdi_gdp_growth_pct",
    "FP.CPI.TOTL.ZG": "wdi_inflation_pct",
    "SP.POP.TOTL": "wdi_population",
    "SP.POP.0014.TO.ZS": "wdi_pop_age_0_14_pct",
    "NY.GDP.TOTL.RT.ZS": "wdi_natural_resource_rents_pct_gdp",
    "SL.UEM.TOTL.ZS": "wdi_unemployment_pct",
}
REGIME = {"Closed autocracy": 0, "Electoral autocracy": 1, "Electoral democracy": 2, "Liberal democracy": 3}

GROUPS: dict[str, list[str]] = {
    "ucdp_history": list(UCDP_FEATURES),
    "neighbours_region": ["nb_count", "nb_sb_events_l1", "nb_sb_events_l12", "nb_sb_deaths_l12", "nb_countries_in_conflict_l12",
                          "region_sb_events_l12", "region_countries_in_conflict_l12"],
    "politics": ["vdem_electoral_democracy", "vdem_liberal_democracy", "vdem_regime_type", "vdem_electoral_democracy_change",
                 "epr_excluded_pop_share", "epr_excluded_groups", "epr_relevant_groups"],
    "economy": ["wdi_log_gdp_per_capita", "wdi_gdp_growth_pct", "wdi_inflation_pct", "wdi_log_population",
                "wdi_pop_age_0_14_pct", "wdi_natural_resource_rents_pct_gdp", "wdi_unemployment_pct"],
    "prices": ["brent_usd", "brent_chg_3m_pct", "food_index", "food_chg_3m_pct", "wheat_chg_3m_pct"],
    "dynamics": [],  # filled by _dynamics_columns(): 1/3/6-month changes of monthly factors, 12-month for yearly ones
    "news": ["news_share_of_world", "news_log_events", "news_violent_share", "news_threat_share", "news_protest_share",
             "news_diplomatic_tension_share", "news_de_escalation_share", "news_avg_goldstein", "news_avg_tone",
             "news_violent_share_3m", "news_coverage_change_3m"],
}


# ------------------------------------------------------------------ helpers
def load_adjacency_isonum() -> dict[str, list[str]]:
    """Land borders by ISO numeric code, derived from Natural Earth 1:50m polygons (topojson neighbours)."""
    from importlib import resources

    with resources.files("app.registry").joinpath("data/adjacency_isonum.json").open() as f:
        return json.load(f)


def neighbours_iso3(session: Session) -> dict[str, list[str]]:
    num_to_iso, _ = _iso_maps(session)
    out: dict[str, list[str]] = {}
    for k, vs in load_adjacency_isonum().items():
        a = num_to_iso.get(int(k))
        if a:
            out[a] = sorted({num_to_iso[int(v)] for v in vs if int(v) in num_to_iso and num_to_iso[int(v)] != a})
    return out


def _iso_maps(session: Session) -> tuple[dict[int, str], dict[str, str]]:
    num = {n: i for i, n in session.execute(select(Country.iso3, Country.iso_numeric)).all() if n}
    gw = dict(session.execute(text(
        "SELECT m.code, c.iso3 FROM source_code_mappings m JOIN countries c ON c.id = m.entity_id "
        "WHERE m.scheme = 'gw' AND m.entity_type = 'country'")).all())
    return num, gw


def _release_year(months: pd.Series, release_month: int) -> pd.Series:
    """Latest data year published by the start of each month (Y-1 from release_month of Y, else Y-2)."""
    return np.where(months.dt.month >= release_month, months.dt.year - 1, months.dt.year - 2)


def universe(session: Session, cm: pd.DataFrame) -> pd.DataFrame:
    """Expand UCDP country-months to every V-Dem country (zeros where UCDP recorded nothing)."""
    vdem = pd.read_csv(FACTOR_DIR / "owid_political-regime.csv")
    isos = set(vdem["code"].dropna()) | set(cm["iso3"])
    known = set(session.scalars(select(Country.iso3).where(Country.is_historical.is_(False))))
    isos = sorted(i for i in isos if isinstance(i, str) and len(i) == 3 and i in known)
    months = pd.date_range(cm["month"].min(), cm["month"].max(), freq="MS")
    idx = pd.MultiIndex.from_product([isos, months], names=["iso3", "month"])
    return cm.set_index(["iso3", "month"]).reindex(idx, fill_value=0).reset_index()


# Speed of change: how fast conditions deteriorate can matter more than their level.
MONTHLY_DYNAMIC = {
    "news_violent_share": "diff", "news_threat_share": "diff", "news_protest_share": "diff",
    "news_diplomatic_tension_share": "diff", "news_de_escalation_share": "diff", "news_avg_goldstein": "diff",
    "news_avg_tone": "diff", "news_share_of_world": "logratio",
    "nb_sb_events_l1": "diff", "region_sb_events_l12": "diff", "os_events_l3": "diff", "ns_events_l3": "diff",
    "brent_usd": "pct", "food_index": "pct",
}
YEARLY_DYNAMIC = ["wdi_log_gdp_per_capita", "wdi_gdp_growth_pct", "wdi_inflation_pct", "wdi_unemployment_pct",
                  "vdem_liberal_democracy", "epr_excluded_pop_share"]
WINDOWS = (1, 3, 6)


def _dynamics_columns(available: set[str]) -> list[str]:
    cols = [f"{c}_chg_{w}m" for c in MONTHLY_DYNAMIC if c in available for w in WINDOWS]
    cols += [f"{c}_chg_12m" for c in YEARLY_DYNAMIC if c in available]
    return cols


def add_dynamics(df: pd.DataFrame) -> pd.DataFrame:
    """Add change-over-time features. Inputs are already lagged (row t holds data up to t-1),
    so the change between row t and row t-k compares (t-1) with (t-1-k): no future data."""
    df = df.sort_values(["iso3", "month"]).reset_index(drop=True)
    g = df.groupby("iso3")
    new = {}
    for c, kind in MONTHLY_DYNAMIC.items():
        if c not in df.columns:
            continue
        for w in WINDOWS:
            prev = g[c].shift(w)
            if kind == "diff":
                new[f"{c}_chg_{w}m"] = df[c] - prev
            elif kind == "pct":
                new[f"{c}_chg_{w}m"] = (df[c] / prev - 1) * 100
            else:  # log ratio of coverage shares, robust to scale
                new[f"{c}_chg_{w}m"] = np.log(df[c] / prev)
    for c in YEARLY_DYNAMIC:
        if c in df.columns:
            new[f"{c}_chg_12m"] = df[c] - g[c].shift(12)
    out = pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)
    return out.replace([np.inf, -np.inf], np.nan)


# ------------------------------------------------------------ feature blocks
def neighbour_features(session: Session, cm: pd.DataFrame) -> pd.DataFrame:
    num_to_iso, _ = _iso_maps(session)
    adj_raw = load_adjacency_isonum()
    adj: dict[str, list[str]] = {}
    for k, vs in adj_raw.items():
        a = num_to_iso.get(int(k))
        if a:
            adj[a] = sorted({num_to_iso[int(v)] for v in vs if int(v) in num_to_iso and num_to_iso[int(v)] != a})
    # lagged own history per country (months < t)
    h = cm.sort_values(["iso3", "month"]).copy()
    g = h.groupby("iso3")
    h["ev_l1"] = g["sb_events"].shift(1)
    h["ev_l12"] = g["sb_events"].transform(lambda s: s.shift(1).rolling(12, min_periods=1).sum())
    h["de_l12"] = g["sb_deaths"].transform(lambda s: s.shift(1).rolling(12, min_periods=1).sum())
    hist = h.set_index(["iso3", "month"])[["ev_l1", "ev_l12", "de_l12"]]
    rows = [(c, n) for c, ns in adj.items() for n in ns]
    pairs = pd.DataFrame(rows, columns=["iso3", "nb"])
    nb = pairs.merge(hist.reset_index().rename(columns={"iso3": "nb"}), on="nb")
    agg = nb.groupby(["iso3", "month"]).agg(
        nb_sb_events_l1=("ev_l1", "sum"), nb_sb_events_l12=("ev_l12", "sum"), nb_sb_deaths_l12=("de_l12", "sum"),
        nb_countries_in_conflict_l12=("ev_l12", lambda s: int((s > 0).sum()))).reset_index()
    agg["nb_count"] = agg["iso3"].map(lambda i: len(adj.get(i, [])))
    # sub-region (UN M49), excluding the country itself
    sub = dict(session.execute(text(
        "SELECT c.iso3, r.code FROM countries c JOIN regions r ON r.id = c.subregion_id")).all())
    h["region"] = h["iso3"].map(sub)
    reg = h.groupby(["region", "month"]).agg(r_ev=("ev_l12", "sum"), r_n=("ev_l12", lambda s: int((s > 0).sum()))).reset_index()
    h = h.merge(reg, on=["region", "month"], how="left")
    h["region_sb_events_l12"] = h["r_ev"] - h["ev_l12"].fillna(0)
    h["region_countries_in_conflict_l12"] = h["r_n"] - (h["ev_l12"].fillna(0) > 0).astype(int)
    out = h[["iso3", "month", "region_sb_events_l12", "region_countries_in_conflict_l12"]].merge(agg, on=["iso3", "month"], how="left")
    # mapped countries without land borders (islands) genuinely have 0 neighbours; unmapped ones stay NaN
    islands = out["iso3"].map(lambda i: i in adj and not adj[i])
    nb_cols = ["nb_count", "nb_sb_events_l1", "nb_sb_events_l12", "nb_sb_deaths_l12", "nb_countries_in_conflict_l12"]
    out.loc[islands, nb_cols] = 0
    return out


def politics_features(session: Session, frame: pd.DataFrame) -> pd.DataFrame:
    keys = frame[["iso3", "month"]].copy()
    keys["vy"] = _release_year(keys["month"], 4)
    ed = pd.read_csv(FACTOR_DIR / "owid_electoral-democracy-index.csv").rename(
        columns={"code": "iso3", "year": "vy", "electdem_vdem__estimate_best": "vdem_electoral_democracy"})
    ld = pd.read_csv(FACTOR_DIR / "owid_liberal-democracy-index.csv").rename(
        columns={"code": "iso3", "year": "vy", "libdem_vdem__estimate_best": "vdem_liberal_democracy"})
    rg = pd.read_csv(FACTOR_DIR / "owid_political-regime.csv").rename(columns={"code": "iso3", "year": "vy"})
    rg["vdem_regime_type"] = rg["regime_row_owid"].map(REGIME)
    v = ed[["iso3", "vy", "vdem_electoral_democracy"]].merge(ld[["iso3", "vy", "vdem_liberal_democracy"]], on=["iso3", "vy"], how="outer") \
        .merge(rg[["iso3", "vy", "vdem_regime_type"]], on=["iso3", "vy"], how="outer").sort_values(["iso3", "vy"])
    v["vdem_electoral_democracy_change"] = v.groupby("iso3")["vdem_electoral_democracy"].diff()
    out = keys.merge(v, on=["iso3", "vy"], how="left")
    # EPR: groups active in year Y-1
    _, gw_to_iso = _iso_maps(session)
    epr = pd.read_csv(FACTOR_DIR / "epr.csv")
    epr["iso3"] = epr["gwid"].astype(str).map(gw_to_iso)
    years = range(int(epr["from"].min()), int(epr["to"].max()) + 1)
    ex = epr[epr["status"].isin(["POWERLESS", "DISCRIMINATED"])]
    rows = []
    for y in years:
        act = epr[(epr["from"] <= y) & (epr["to"] >= y)]
        exa = ex[(ex["from"] <= y) & (ex["to"] >= y)]
        a = act.groupby("iso3").size().rename("epr_relevant_groups")
        e = exa.groupby("iso3").agg(epr_excluded_pop_share=("size", "sum"), epr_excluded_groups=("size", "size"))
        df = pd.concat([a, e], axis=1).reset_index()
        df["ey"] = y
        rows.append(df)
    eprm = pd.concat(rows)
    eprm[["epr_excluded_pop_share", "epr_excluded_groups"]] = eprm[["epr_excluded_pop_share", "epr_excluded_groups"]].fillna(0)
    out["ey"] = out["month"].dt.year - 1
    out = out.merge(eprm, on=["iso3", "ey"], how="left")
    return out[["iso3", "month", *GROUPS["politics"]]]


def economy_features(frame: pd.DataFrame) -> pd.DataFrame:
    keys = frame[["iso3", "month"]].copy()
    keys["wy"] = _release_year(keys["month"], 7)
    vals = []
    for code, name in WDI.items():
        data = json.loads((FACTOR_DIR / f"wdi_{code}.json").read_text())[1] or []
        vals.append(pd.DataFrame([{"iso3": d["countryiso3code"], "wy": int(d["date"]), name: d["value"]}
                                  for d in data if d["countryiso3code"]]))
    w = vals[0]
    for v in vals[1:]:
        w = w.merge(v, on=["iso3", "wy"], how="outer")
    w["wdi_log_gdp_per_capita"] = np.log(w["wdi_gdp_per_capita_const_usd"])
    w["wdi_log_population"] = np.log(w["wdi_population"])
    out = keys.merge(w, on=["iso3", "wy"], how="left")
    return out[["iso3", "month", *GROUPS["economy"]]]


def price_features(frame: pd.DataFrame) -> pd.DataFrame:
    def fred(sid: str, name: str) -> pd.DataFrame:
        df = pd.read_csv(FACTOR_DIR / f"fred_{sid}.csv")
        df.columns = ["month", name]
        df["month"] = pd.to_datetime(df["month"])
        df[name] = pd.to_numeric(df[name], errors="coerce")
        return df.set_index("month")

    p = pd.concat([fred("MCOILBRENTEU", "brent"), fred("PFOODINDEXM", "food"), fred("PWHEAMTUSDM", "wheat")], axis=1)
    p = p.reindex(pd.date_range(p.index.min(), frame["month"].max(), freq="MS"))
    f = pd.DataFrame(index=p.index)
    f["brent_usd"] = p["brent"].shift(1)  # month t-1
    f["brent_chg_3m_pct"] = (p["brent"].shift(1) / p["brent"].shift(4) - 1) * 100
    f["food_index"] = p["food"].shift(1)
    f["food_chg_3m_pct"] = (p["food"].shift(1) / p["food"].shift(4) - 1) * 100
    f["wheat_chg_3m_pct"] = (p["wheat"].shift(1) / p["wheat"].shift(4) - 1) * 100
    f = f.rename_axis("month").reset_index()
    return frame[["iso3", "month"]].merge(f, on="month", how="left")


def news_features(session: Session, frame: pd.DataFrame) -> pd.DataFrame:
    from app.ml.news_history import monthly_frame

    n = monthly_frame(session)
    if n.empty:
        return frame[["iso3", "month"]].assign(**{c: np.nan for c in GROUPS["news"]})
    n = n.sort_values(["iso3", "month"])
    ev = n["events"].replace(0, np.nan)
    feats = pd.DataFrame({
        "iso3": n["iso3"], "month": n["month"],
        "news_share_of_world": n["events"] / n["world_events"],
        "news_log_events": np.log1p(n["events"]),
        "news_violent_share": n["violent"] / ev, "news_threat_share": n["threats"] / ev,
        "news_protest_share": n["protests"] / ev, "news_diplomatic_tension_share": n["diplomatic_tension"] / ev,
        "news_de_escalation_share": n["de_escalation"] / ev, "news_avg_goldstein": n["goldstein_sum"] / ev,
        "news_avg_tone": n["tone_sum"] / ev, "_violent": n["violent"], "_events": n["events"],
    })
    # full monthly grid so shifts are calendar-correct; months without coverage stay NaN
    grid = frame[["iso3", "month"]].drop_duplicates()
    feats = grid.merge(feats, on=["iso3", "month"], how="left").sort_values(["iso3", "month"])
    g = feats.groupby("iso3")
    v3 = g["_violent"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    e3 = g["_events"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    feats["news_violent_share_3m"] = v3 / e3.replace(0, np.nan)
    share = feats["news_share_of_world"]
    prev = g["news_share_of_world"].transform(lambda s: s.shift(3).rolling(3, min_periods=1).mean())
    feats["news_coverage_change_3m"] = share.groupby(feats["iso3"]).transform(lambda s: s.rolling(3, min_periods=1).mean()) / prev - 1
    # lag everything by one month: row t sees news up to the end of t-1
    cols = GROUPS["news"]
    feats[cols] = feats.groupby("iso3")[cols].shift(1)
    return feats[["iso3", "month", *cols]]


# ----------------------------------------------------------------- dataset
def onset_labels(cm: pd.DataFrame) -> pd.DataFrame:
    """Clean "new war" labels (``onset-major-v1``).

    * ``onset_risk_major`` — no UCDP state-based deaths in the previous 24 months (t-24..t-1)
    * ``onset_major``      — for those rows: >= 25 deaths (best estimate) in months t..t+2; NaN when the window
                             is not yet fully observed (e.g. forecast rows)
    * ``minor_incident``   — fighting in t..t+2 but < 25 deaths (kept for analysis, not a "new war")
    """
    out = []
    for iso, g in cm.sort_values("month").groupby("iso3"):
        g = g.reset_index(drop=True)
        prev24 = g["sb_deaths"].shift(1).rolling(24, min_periods=24).sum()
        nxt = lambda col: g[col][::-1].rolling(3, min_periods=3).sum()[::-1]  # noqa: E731 - t..t+2
        d3, e3 = nxt("sb_deaths"), nxt("sb_events")
        risk = prev24 == 0
        out.append(pd.DataFrame({
            "iso3": iso, "month": g["month"], "onset_risk_major": risk.astype(int),
            "deaths_next_3m": d3,
            "onset_major": np.where(risk & d3.notna(), (d3 >= 25).astype(float), np.nan),
            "minor_incident": np.where(risk & d3.notna(), ((e3 > 0) & (d3 < 25)).astype(float), np.nan),
        }))
    return pd.concat(out, ignore_index=True)


def ucdp_history(session: Session) -> tuple[pd.DataFrame, dict]:
    """Official GED + newer Candidate months, expanded to every country; returns (frame, provenance)."""
    from app.ingestion.connectors.ucdp import latest_candidate

    dv, cand = latest_version(session), latest_candidate(session)
    cm = universe(session, ucdp_country_months(session, dv.id, cand.id if cand else None))
    prov = {"ucdp_official": dv.version, "ucdp_candidate": cand.version if cand else None,
            "data_through": cm["month"].max().strftime("%Y-%m")}
    return cm, prov


def build_enriched(session: Session, include_news: bool = True, forecast: bool = False) -> pd.DataFrame:
    """Enriched country-month table. ``forecast=True`` appends one row per country for the month after the
    last observed UCDP month (features only; labels unknown)."""
    cm, prov = ucdp_history(session)
    if forecast:
        nxt = cm["month"].max() + pd.DateOffset(months=1)
        extra = pd.DataFrame({"iso3": sorted(cm["iso3"].unique()), "month": nxt})
        cm = pd.concat([cm, extra], ignore_index=True)  # count columns NaN = not yet observed
    base = build_frame(cm, 1, keep_unlabeled=forecast)
    out = base.merge(neighbour_features(session, cm), on=["iso3", "month"], how="left")
    out = out.merge(politics_features(session, out), on=["iso3", "month"], how="left")
    out = out.merge(economy_features(out), on=["iso3", "month"], how="left")
    out = out.merge(price_features(out), on=["iso3", "month"], how="left")
    if include_news:
        out = out.merge(news_features(session, out), on=["iso3", "month"], how="left")
    out = add_dynamics(out)
    GROUPS["dynamics"] = _dynamics_columns(set(out.columns))
    out["is_onset_risk_row"] = (out["sb_events_l12"] == 0).astype(int)  # no fighting in previous 12 months
    out = out.merge(onset_labels(cm), on=["iso3", "month"], how="left")
    assert len(out) == len(base), "enrichment must not add or drop rows"
    out.attrs["provenance"] = prov
    return out


# --------------------------------------------------------------- evaluation
def _boot_ci(y: np.ndarray, p: np.ndarray, fn, n: int = 300) -> tuple[float, float]:
    rng = np.random.default_rng(SEED)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if 0 < y[i].sum() < len(y):
            vals.append(fn(y[i], p[i]))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))) if vals else (np.nan, np.nan)


def evaluate(df: pd.DataFrame, features: list[str], test_years: list[int], target: str = TARGET,
             risk_col: str = "is_onset_risk_row", make_model=None) -> dict:
    """Walk-forward evaluation. Ranking metrics (ROC/PR-AUC, top-20) use the model's raw scores, which a
    calibrator cannot flatten; Brier/log-loss use calibrated probabilities. The isotonic calibrator is fitted
    on the last 3 training years (rare events need more than one year of positives); if those years hold
    fewer than 3 positives, raw scores are used as probabilities for that fold."""
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import average_precision_score, roc_auc_score

    preds = []
    for year in test_years:
        train = df[df["month"].dt.year < year]
        test = df[df["month"].dt.year == year]
        fit, calib = train[train["month"].dt.year < year - 3], train[train["month"].dt.year >= year - 3]
        pos = fit[target].mean()
        model = make_model() if make_model else _lgbm((1 - pos) / max(pos, 1e-6))
        model.fit(fit[features], fit[target].astype(int))
        raw = model.predict_proba(test[features])[:, 1]
        if calib[target].sum() >= 3:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1)
            iso.fit(model.predict_proba(calib[features])[:, 1], calib[target].astype(int))
            p = iso.predict(raw)
        else:
            p = raw
        preds.append(test[["iso3", "month", target, risk_col]].assign(p=p, raw=raw, year=year))
    allp = pd.concat(preds).rename(columns={target: TARGET, risk_col: "is_onset_risk_row"})
    res = {}
    for name, sub in (("all", allp), ("onset", allp[allp.is_onset_risk_row == 1]), ("ongoing", allp[allp.is_onset_risk_row == 0])):
        if sub.empty:
            continue
        y, p, r = sub[TARGET].astype(int).to_numpy(), sub["p"].to_numpy(), sub["raw"].to_numpy()
        m = _metrics(y, p)
        m.pop("calibration", None)
        if 0 < y.sum() < len(y):
            m["roc_auc"], m["pr_auc"] = float(roc_auc_score(y, r)), float(average_precision_score(y, r))
            m["pr_auc_ci95"] = _boot_ci(y, r, average_precision_score)
            m["roc_auc_ci95"] = _boot_ci(y, r, roc_auc_score)
        res[name] = m
    # top-20 per month among onset-risk countries, ranked by raw score
    on = allp[allp.is_onset_risk_row == 1]
    top = on.sort_values("raw", ascending=False).groupby("month").head(20)
    res["onset_top20_monthly"] = {"flagged": int(len(top)), "hits": int(top[TARGET].sum()),
                                  "share_of_all_onsets_caught": float(top[TARGET].sum() / max(on[TARGET].sum(), 1)),
                                  "precision": float(top[TARGET].mean()) if len(top) else None}
    return res, allp


def run_comparison(session: Session, test_years: list[int] | None = None) -> dict:
    settings = get_settings()
    df = build_enriched(session, include_news=True)
    test_years = test_years or list(range(2019, 2026))
    variants = {
        "A_ucdp_only": GROUPS["ucdp_history"],
        "B_plus_structural": GROUPS["ucdp_history"] + GROUPS["neighbours_region"] + GROUPS["politics"] + GROUPS["economy"],
        "C_plus_prices": GROUPS["ucdp_history"] + GROUPS["neighbours_region"] + GROUPS["politics"] + GROUPS["economy"] + GROUPS["prices"],
        "D_plus_news": [c for k, g in GROUPS.items() if k != "dynamics" for c in g],
        "E_plus_dynamics": [c for g in GROUPS.values() for c in g],
    }
    # dataset version
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    checksum = hashlib.sha256(buf.getvalue()).hexdigest()
    version = f"enriched-v1-{checksum[:10]}"
    path = Path(settings.model_dir) / "datasets" / f"country_month_enriched_{version}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    ds = session.scalar(select(TrainingDataset).where(TrainingDataset.name == "country_month_enriched",
                                                      TrainingDataset.version == version))
    if ds is None:
        ds = TrainingDataset(
            name="country_month_enriched", version=version, row_count=len(df), file_path=str(path), file_format="parquet",
            checksum=checksum,
            definition={"groups": GROUPS, "target": f"{TARGET} in month t", "onset_subset": "no UCDP fighting in t-12..t-1",
                        "lags": {"ucdp/neighbours": "months < t", "news/prices": "t-1", "vdem": "Y-1 from April",
                                 "wdi": "Y-1 from July", "epr": "Y-1"},
                        "pit_mode": "research_final_release", "universe": "all V-Dem countries + UCDP countries"},
            licensing={"sources": ["ucdp_ged", "gdelt_v1_history", "V-Dem via OWID (CC BY)", "World Bank WDI (CC BY 4.0)",
                                   "EPR (ETH Zurich)", "FRED/IMF commodity prices"]})
        session.add(ds)
        session.flush()
    results = {"dataset": version, "rows": len(df), "countries": int(df.iso3.nunique()),
               "onset_risk_rows": int(df.is_onset_risk_row.sum()),
               "onsets": int(df[(df.is_onset_risk_row == 1)][TARGET].sum()), "variants": {}}
    for name, feats in variants.items():
        res, _ = evaluate(df, feats, test_years)
        results["variants"][name] = res
        mv = ModelVersion(name=f"conflict_next_month_{name}", version=f"{version}-s{SEED}", algorithm="lightgbm+isotonic",
                          params={"features": feats}, training_dataset_id=ds.id, random_seed=SEED, status="experimental",
                          notes=f"Walk-forward {test_years[0]}-{test_years[-1]}; metrics pooled over test years.")
        old = session.scalar(select(ModelVersion).where(ModelVersion.name == mv.name, ModelVersion.version == mv.version))
        if old:
            session.delete(old)
            session.flush()
        session.add(mv)
        session.flush()
        session.add(ModelEvaluation(model_version_id=mv.id, split_kind="walk_forward_pooled", fold=0,
                                    metrics={"all": res["all"], "onset": res["onset"], "ongoing": res["ongoing"],
                                             "onset_top20_monthly": res["onset_top20_monthly"]},
                                    baseline_metrics={}, notes="Subsets: onset = no fighting in previous 12 months."))
        log.info("variant_done", variant=name, onset_pr_auc=res["onset"].get("pr_auc"), all_pr_auc=res["all"].get("pr_auc"))
    session.flush()
    results["feature_importance_D"] = feature_importance(df, variants["E_plus_dynamics"])
    return results


def feature_importance(df: pd.DataFrame, features: list[str]) -> list[tuple[str, float]]:
    pos = df[TARGET].mean()
    m = _lgbm((1 - pos) / pos)
    m.fit(df[features], df[TARGET].astype(int))
    imp = m.booster_.feature_importance(importance_type="gain")
    total = imp.sum() or 1
    return sorted(((f, round(float(v / total), 4)) for f, v in zip(features, imp, strict=True)), key=lambda x: -x[1])[:20]


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for g in GROUPS.values() for c in g if c in df.columns]
    return pd.DataFrame({"column": cols, "null_pct": [round(100 * df[c].isna().mean(), 1) for c in cols]})
