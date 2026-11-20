"""Crisis index: one 0-100 score per country from five dimensions, not deaths alone.

Dimensions (each 0-100):

* **violence**   — verified deaths from organised violence (UCDP, past 12 months; log scale: 10 → 25, 100 → 50,
                   1,000 → 75, 10,000+ → 100), raised (never lowered) by the share of the country's news about
                   fighting and attacks (CAMEO 18-20);
* **military**   — share of news about military posture, coercion, blockades, arrests and cut relations (CAMEO 15-17);
* **tension**    — share of news about threats, demands, rejections, sanctions and diplomatic disputes (CAMEO 10-13, 16);
* **unrest**     — share of news about protests (CAMEO 14);
* **momentum**   — violent + military + tension news in the latest month compared with the country's own previous 12
                   months, corrected for the worldwide change (50 = normal, 100 = twice as much or more, 0 = half or less).

News dimensions use **shares of the country's own news**, so heavily covered countries are not high just because of
volume, and are turned into percentiles against all country-months 1990-2025 (e.g. 95 = higher than 95% of history).
Countries with fewer than 300 news events in the period have no news dimensions; their score uses violence only.

The score starts from the strongest dimension and the weighted mean of all five (default weights 40/20/20/10/10,
editable in the admin panel as ``crisis_index_weights``) adds to it, so an extreme dimension counts on its own and
breadth adds to it. The level comes from cut-offs on the historical distribution (Critical = top 3% of country-months, High = next 7%,
Moderate = next 15%).
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

DIMENSIONS = ["violence", "military", "tension", "unrest", "momentum"]
DEFAULT_WEIGHTS = {"violence": 0.4, "military": 0.2, "tension": 0.2, "unrest": 0.1, "momentum": 0.1}
SHARES = {"violent": ("v",), "military": ("mc", "-v"), "tension": ("th", "dt"), "unrest": ("pr",)}
MIN_EVENTS = 300
LEVEL_QUANTILES = {"critical": 0.97, "high": 0.90, "elevated": 0.75}
LEVEL_WORDS = {"critical": "Critical", "high": "High", "elevated": "Moderate", "low": "Low"}
DIMENSION_LABELS = {"violence": "Violence", "military": "Military activity", "tension": "Tension",
                    "unrest": "Unrest", "momentum": "Change vs normal"}

_cache: dict = {}


# ------------------------------------------------------------------ building blocks
def deaths_score(annual_deaths) -> np.ndarray:
    return np.clip(25 * np.log10(1 + np.asarray(annual_deaths, dtype=float)), 0, 100)


def momentum_score(ratio) -> np.ndarray:
    r = np.asarray(ratio, dtype=float)
    return np.clip(50 + 50 * np.log2(np.where(r > 0, r, np.nan)), 0, 100)


# World average shares 1990-2025 (GDELT 1.0) and the strength of the pull towards them. A country with little news
# needs more evidence to score high: shares are estimated as (count + K * world share) / (events + K), so a few
# stories about arrests in a small country do not look like a military crisis, while countries with tens of
# thousands of events are unaffected.
PRIOR = {"violent": 0.0806, "military": 0.0615, "tension": 0.1275, "unrest": 0.0095, "conflict": 0.2696}
PRIOR_EVENTS = 2000


def shares(df: pd.DataFrame) -> pd.DataFrame:
    """News shares per row from summed counts (columns ev, v, mc, th, dt, pr), shrunk towards the world average."""
    ok = df["ev"] >= MIN_EVENTS
    ev = df["ev"].where(ok)
    k = PRIOR_EVENTS

    def est(count, name):
        return (count + k * PRIOR[name]) / (ev + k)

    return pd.DataFrame({
        "violent": est(df["v"], "violent"), "military": est(df["mc"] - df["v"], "military"),
        "tension": est(df["th"] + df["dt"], "tension"), "unrest": est(df["pr"], "unrest"),
        "conflict": est(df["mc"] + df["th"] + df["dt"], "conflict"),   # used for momentum
    }, index=df.index)


def material_share(df: pd.DataFrame) -> pd.Series:
    """Share of news about fighting and military action (CAMEO 15-20), shrunk towards the world average."""
    ev = df["ev"].where(df["ev"] >= MIN_EVENTS)
    return (df["mc"] + PRIOR_EVENTS * (PRIOR["violent"] + PRIOR["military"])) / (ev + PRIOR_EVENTS)


def _monthly_news(session: Session) -> pd.DataFrame:
    df = pd.read_sql(text(
        "SELECT iso3, month, sum(events) AS ev, sum(violent) AS v, sum(material_conflict) AS mc, sum(threats) AS th, "
        "sum(diplomatic_tension) AS dt, sum(protests) AS pr FROM news_country_month_parts GROUP BY 1, 2"),
        session.connection())
    df["month"] = pd.to_datetime(df["month"])
    return df


def reference(session: Session) -> dict:
    """Sorted historical values (1990-2025) of each news share, and the composite cut-offs for the levels."""
    if "ref" in _cache and time.time() - _cache["ref_at"] < 86_400:
        return _cache["ref"]
    n = _monthly_news(session)
    hist = n[(n["iso3"] != "WLD") & (n["month"] >= "1990-01-01") & (n["month"] < "2026-01-01")]
    sh = shares(hist)
    ref = {k: np.sort(sh[k].dropna().to_numpy()) for k in ("violent", "military", "tension", "unrest")}
    _cache.update(ref=ref, ref_at=time.time())
    return ref


def pct(ref: np.ndarray, values) -> np.ndarray:
    v = np.asarray(values, dtype=float)
    out = np.searchsorted(ref, v, side="right") / max(len(ref), 1) * 100
    return np.where(np.isnan(v), np.nan, out)


# How much a single dimension can carry the score on its own. Verified violence counts fully; military activity and
# tension come from news only (noisier), so they count a bit less; protests less still.
PEAK_FACTOR = {"violence": 1.0, "military": 0.75, "tension": 0.75, "unrest": 0.6}
BREADTH = 0.3   # how much the other dimensions can add on top of the strongest one


def compose(dims: pd.DataFrame, weights: dict | None = None) -> pd.Series:
    """Strongest dimension first (scaled by PEAK_FACTOR), then the weighted mean of all available dimensions fills
    part of the remaining gap to 100: score = peak + BREADTH x (100 - peak) x mean / 100. An extreme dimension is
    serious on its own (a full war, a military stand-off), breadth across dimensions adds to it, and calm dimensions
    never pull a serious one down. Missing dimensions are left out and the weights re-normalised."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    num = sum(dims[k].fillna(0) * w[k] for k in DIMENSIONS)
    den = sum(dims[k].notna() * w[k] for k in DIMENSIONS)
    mean = num / den.replace(0, np.nan)
    peak = pd.concat([dims[k] * f for k, f in PEAK_FACTOR.items()], axis=1).max(axis=1)
    return (peak + BREADTH * (100 - peak) * mean / 100).round(1)


def dimension_frame(sh: pd.DataFrame, annual_deaths, ratio, ref: dict) -> pd.DataFrame:
    viol_news = pct(ref["violent"], sh["violent"])
    d = deaths_score(annual_deaths)
    # verified deaths are the base; violent news can add (up to 40% of the gap) but never lowers verified deaths
    violence = np.where(np.isnan(viol_news), d, d + 0.4 * np.clip(viol_news - d, 0, None))
    return pd.DataFrame({
        "violence": violence, "military": pct(ref["military"], sh["military"]),
        "tension": pct(ref["tension"], sh["tension"]), "unrest": pct(ref["unrest"], sh["unrest"]),
        "momentum": momentum_score(ratio),
    }, index=sh.index).round(1)


def level_cuts(scores: pd.Series) -> dict[str, float]:
    s = scores.dropna()
    return {lv: round(float(s.quantile(q)), 1) for lv, q in LEVEL_QUANTILES.items()}


def level_of(score: float | None, cuts: dict[str, float]) -> str:
    if score is None or (isinstance(score, float) and np.isnan(score)):
        return "low"
    for lv in ("critical", "high", "elevated"):
        if score >= cuts[lv]:
            return lv
    return "low"


# ------------------------------------------------------------------ history (for training and cut-offs)
def historical(session: Session, cm: pd.DataFrame, weights: dict | None = None) -> pd.DataFrame:
    """Index per country-month as known at the start of month t: news of month t-1, deaths of t-12..t-1, momentum of
    t-1 vs t-13..t-2. Also the index of the next 3 / 6 months (news and deaths of t..t+h-1) for labels."""
    ref = reference(session)
    n = _monthly_news(session)
    world = n[n["iso3"] == "WLD"].set_index("month").sort_index()
    wsh = shares(world.assign(ev=world["ev"]))["conflict"]
    wnorm = wsh.shift(1).rolling(12, min_periods=6).mean()
    wratio = (wsh / wnorm).rename("wratio")
    n = n[n["iso3"] != "WLD"].sort_values(["iso3", "month"])
    grid = cm[["iso3", "month"]].drop_duplicates()
    n = grid.merge(n, on=["iso3", "month"], how="left").sort_values(["iso3", "month"])
    deaths = cm[["iso3", "month", "all_deaths"]]
    n = n.merge(deaths, on=["iso3", "month"], how="left")
    g = n.groupby("iso3")
    sh = shares(n.fillna({"ev": 0}))
    n["conflict_share"] = sh["conflict"]
    norm = g["conflict_share"].transform(lambda x: x.shift(1).rolling(12, min_periods=6).mean())
    n = n.merge(wratio, left_on="month", right_index=True, how="left")
    n["ratio"] = n["conflict_share"] / norm / n["wratio"]

    # short-term surge: fighting + military news (material conflict) of the latest month vs the 3 months before,
    # corrected for the worldwide change; catches fresh escalation that the 12-month normal can hide
    n["mat_share"] = material_share(n.fillna({"ev": 0}))
    wmat = material_share(world).rename("wmat")
    wsurge = (wmat / wmat.shift(1).rolling(3, min_periods=2).mean()).rename("wsurge")
    n = n.merge(wsurge, left_on="month", right_index=True, how="left")
    base3 = n.groupby("iso3")["mat_share"].transform(lambda x: x.shift(1).rolling(3, min_periods=2).mean())
    n["surge"] = n["mat_share"] / base3 / n["wsurge"]

    out = n[["iso3", "month"]].copy()
    out["idx_recent_surge"] = n.groupby("iso3")["surge"].shift(1).to_numpy()  # row t sees month t-1
    # "now" at month t: news of t-1, deaths over t-12..t-1, momentum of t-1
    lag = n.groupby("iso3")[["ev", "v", "mc", "th", "dt", "pr", "ratio"]].shift(1)
    deaths12 = g["all_deaths"].transform(lambda x: x.shift(1).rolling(12, min_periods=1).sum())
    now = dimension_frame(shares(lag.fillna({"ev": 0})), deaths12.fillna(0), lag["ratio"], ref)
    out[[f"idx_{k}" for k in DIMENSIONS]] = now.to_numpy()
    out["idx_score"] = compose(now, weights).to_numpy()
    # future windows t..t+h-1 (labels): summed news, annualised deaths, momentum of the window vs the normal at t
    for h in (3, 6):
        fwd = {c: g[c].transform(lambda x, h=h: x[::-1].rolling(h, min_periods=h).sum()[::-1])
               for c in ("ev", "v", "mc", "th", "dt", "pr", "all_deaths")}
        fdf = pd.DataFrame(fwd)
        fsh = shares(fdf.fillna({"ev": 0}))
        fratio = fsh["conflict"] / norm / n["wratio"]
        fut = dimension_frame(fsh, fdf["all_deaths"] * 12 / h, fratio, ref)
        out[f"idx_score_next_{h}m"] = compose(fut, weights).where(fdf["all_deaths"].notna()).to_numpy()
    return out


# ------------------------------------------------------------------ live (for the site)
def live(session: Session, today: date | None = None, weights: dict | None = None) -> dict[str, dict]:
    """Index per country now: news of the last 30 days (GDELT daily files), verified deaths of the latest 12 months
    (UCDP), momentum vs the country's previous 12 months corrected for the worldwide change."""
    from app.core.runtime_settings import get_value
    from app.monitoring.plain import conflict_deaths_12m

    today = today or date.today()
    weights = weights or get_value(session, "crisis_index_weights")
    key = ("live", today, tuple(sorted((weights or {}).items())))
    if key in _cache and time.time() - _cache[key][0] < 3600:
        return _cache[key][1]
    ref = reference(session)
    w0 = today - timedelta(days=30)
    rows = pd.read_sql(text(
        "WITH f AS (SELECT iso3, to_date(left(file_key, 8), 'YYYYMMDD') AS d, events, violent, material_conflict, "
        "  threats, diplomatic_tension, protests FROM news_country_month_parts WHERE file_key ~ '^[0-9]{8}[.]' "
        "  AND to_date(left(file_key, 8), 'YYYYMMDD') >= :b0 AND to_date(left(file_key, 8), 'YYYYMMDD') < :t) "
        "SELECT iso3, d >= :w0 AS recent, sum(events) AS ev, sum(violent) AS v, sum(material_conflict) AS mc, "
        "  sum(threats) AS th, sum(diplomatic_tension) AS dt, sum(protests) AS pr FROM f GROUP BY 1, 2"),
        session.connection(), params={"b0": w0 - timedelta(days=365), "w0": w0, "t": today})
    if rows.empty:
        return {}
    recent = rows[rows["recent"]].set_index("iso3")
    before = rows[~rows["recent"]].set_index("iso3")
    sh_now = shares(recent)
    before_conflict = shares(before)["conflict"].where(before["ev"] >= 5 * MIN_EVENTS)
    wr = (sh_now.at["WLD", "conflict"] / before_conflict.at["WLD"]
          if "WLD" in sh_now.index and "WLD" in before_conflict.index else 1.0)
    ratio = (sh_now["conflict"] / before_conflict.reindex(sh_now.index)) / wr
    surge = recent_surge(session, today)
    deaths = conflict_deaths_12m(session)
    isos = [i for i in sh_now.index if i != "WLD"]
    sh_now = sh_now.loc[isos]
    dims = dimension_frame(sh_now, [deaths.get(i, 0) for i in isos], ratio.reindex(isos), ref)
    # countries without enough news still get a violence score from deaths
    missing = sorted(set(deaths) - set(isos))
    if missing:
        extra = pd.DataFrame({"violence": deaths_score([deaths[i] for i in missing])}, index=missing)
        dims = pd.concat([dims, extra.reindex(columns=DIMENSIONS)])
    scores = compose(dims, weights)
    cuts = cuts_for(session, weights)
    out = {}
    for iso in dims.index:
        sc = scores.get(iso)
        out[iso] = {"score": None if pd.isna(sc) else float(sc), "level": level_of(sc, cuts),
                    "dimensions": {k: (None if pd.isna(dims.at[iso, k]) else float(dims.at[iso, k])) for k in DIMENSIONS},
                    "deaths_12m": deaths.get(iso, 0), "news_events_30d": int(recent.at[iso, "ev"]) if iso in recent.index else 0,
                    "recent_surge": surge.get(iso)}
    _cache[key] = (time.time(), out)
    return out


SURGE_SHORT_DAYS, SURGE_BASE_DAYS = 14, 90
SURGE_MIN_EVENTS = 1000   # news events in the last 14 days; fewer is too noisy to call a surge


def recent_surge(session: Session, today: date) -> dict[str, float]:
    """Fighting + military news of the last 14 days vs the 90 days before, per country, corrected for the worldwide
    change (1.0 = as before; 2.0 = twice as much). From the GDELT daily files."""
    t0 = today - timedelta(days=SURGE_SHORT_DAYS)
    b0 = t0 - timedelta(days=SURGE_BASE_DAYS)
    rows = pd.read_sql(text(
        "WITH f AS (SELECT iso3, to_date(left(file_key, 8), 'YYYYMMDD') AS d, events, material_conflict "
        "  FROM news_country_month_parts WHERE file_key ~ '^[0-9]{8}[.]' "
        "  AND to_date(left(file_key, 8), 'YYYYMMDD') >= :b0 AND to_date(left(file_key, 8), 'YYYYMMDD') < :t) "
        "SELECT iso3, d >= :t0 AS short, sum(events) AS ev, sum(material_conflict) AS mc FROM f GROUP BY 1, 2"),
        session.connection(), params={"b0": b0, "t0": t0, "t": today})
    if rows.empty:
        return {}
    short = rows[rows["short"]].set_index("iso3")
    short = short[(short["ev"] >= SURGE_MIN_EVENTS) | (short.index == "WLD")]
    base = rows[~rows["short"]].set_index("iso3")
    s_short, s_base = material_share(short), material_share(base)
    if "WLD" not in s_short.index or "WLD" not in s_base.index:
        return {}
    w = s_short["WLD"] / s_base["WLD"]
    ratio = (s_short / s_base.reindex(s_short.index)) / w
    return {i: round(float(v), 2) for i, v in ratio.items() if i != "WLD" and pd.notna(v)}


def cuts_for(session: Session, weights: dict | None = None) -> dict[str, float]:
    """Level cut-offs from the historical index distribution (cached for a day)."""
    key = ("cuts", tuple(sorted((weights or {}).items())))
    if key in _cache and time.time() - _cache[key][0] < 86_400:
        return _cache[key][1]
    from app.ml.enrichment import ucdp_history

    cm, _ = ucdp_history(session)
    h = historical(session, cm, weights)
    h = h[(h["month"] >= "1990-01-01") & (h["month"] < "2026-01-01")]
    cuts = level_cuts(h["idx_score"])
    _cache[key] = (time.time(), cuts)
    return cuts


def snapshot_history(session: Session) -> pd.DataFrame:
    """The daily country snapshots the site has stored (``feature_snapshots``, entity ``country_index``), one row per
    country and day: score, level, dim_*, deaths_12m, news_events_30d, recent_surge, trend, situations."""
    rows = session.execute(text(
        "SELECT entity_key AS iso3, as_of, features FROM feature_snapshots "
        "WHERE entity_type='country_index' AND feature_set_version='crisis_index_v1' ORDER BY as_of")).all()
    if not rows:
        return pd.DataFrame(columns=["iso3", "date"])
    df = pd.DataFrame([{"iso3": r.iso3, "date": pd.Timestamp(r.as_of).tz_localize(None).normalize(), **r.features}
                       for r in rows])
    return df
