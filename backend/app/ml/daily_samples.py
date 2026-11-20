"""Weekly training samples 2014-2026 from the GDELT 1.0 daily files, with short-term (7 / 14-day) news signals.

The monthly training table only sees news month by month, so a burst of fighting in the last week cannot teach the
model anything. The daily archive (April 2013 →) lets us build, for every country and every week:

* the crisis index **as the site computes it live** (news of the 30 days before the date, deaths of the previous 12
  months, change vs the country's previous year, corrected for the worldwide change);
* short-term signals: fighting / military news of the last 7 and 14 days vs the 90 days before, tension and protests
  of the last 14 days vs before, and attention (news volume) of the last 14 days vs before;
* labels: the index level over the next 3 / 6 months (news of the window, annualised deaths) vs the level now.

Monthly factors (UCDP history, neighbours, politics, economy, news dynamics) are joined from the monthly table for the
month of the sample (they only use data before that month).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ml import crisis_index as CI

COLS = ["ev", "v", "mc", "th", "dt", "pr"]
SHORT_FEATURES = ["st_surge7", "st_surge14", "st_tension14", "st_protest14", "st_attention14", "st_violent7"]
HORIZON_DAYS = {3: 91, 6: 182}
STEP_DAYS = 7


def daily_news(session: Session) -> tuple[pd.DatetimeIndex, dict[str, np.ndarray]]:
    """Per country (and WLD) an array days x 6 of news counts by publication day; missing days are 0."""
    df = pd.read_sql(text(
        "SELECT iso3, to_date(left(file_key, 8), 'YYYYMMDD') AS d, sum(events) AS ev, sum(violent) AS v, "
        "sum(material_conflict) AS mc, sum(threats) AS th, sum(diplomatic_tension) AS dt, sum(protests) AS pr "
        "FROM news_country_month_parts WHERE file_key ~ '^[0-9]{8}[.]' GROUP BY 1, 2"), session.connection())
    df["d"] = pd.to_datetime(df["d"])
    days = pd.date_range(df["d"].min(), df["d"].max(), freq="D")
    out = {}
    for iso, g in df.groupby("iso3"):
        a = np.zeros((len(days), len(COLS)))
        a[days.get_indexer(g["d"])] = g[COLS].to_numpy(dtype=float)
        out[iso] = a
    return days, out


def _csum(a: np.ndarray) -> np.ndarray:
    return np.vstack([np.zeros((1, a.shape[1])), np.cumsum(a, axis=0)])


def _win(cs: np.ndarray, i: np.ndarray, start: int, end: int) -> np.ndarray:
    """Sums over days [i+start, i+end) for each sample position i (start/end relative, end exclusive)."""
    lo = np.clip(i + start, 0, len(cs) - 1)
    hi = np.clip(i + end, 0, len(cs) - 1)
    return cs[hi] - cs[lo]


def _frame(sums: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(sums, columns=COLS)


def _ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.replace(0, np.nan)


def build(session: Session, monthly: pd.DataFrame, cm: pd.DataFrame, sample_days: list[date] | None = None,
          weights: dict | None = None) -> pd.DataFrame:
    """Samples every 7 days (or on ``sample_days``), with daily-index features, short-term signals and labels."""
    ref = CI.reference(session)
    days, news = daily_news(session)
    if sample_days is None:
        pos = np.arange(395, len(days), STEP_DAYS)
    else:
        pos = np.array([days.get_loc(pd.Timestamp(d)) if pd.Timestamp(d) in days else len(days) for d in sample_days])
    # world windows
    wcs = _csum(news["WLD"])
    W = {name: CI.shares(_frame(_win(wcs, pos, a, b))) for name, (a, b) in {
        "now30": (-30, 0), "norm": (-395, -30), "s7": (-7, 0), "b7": (-97, -7), "s14": (-14, 0), "b14": (-104, -14),
        "f3": (0, HORIZON_DAYS[3]), "f6": (0, HORIZON_DAYS[6])}.items()}
    wev14, wevb14 = _win(wcs, pos, -14, 0)[:, 0], _win(wcs, pos, -104, -14)[:, 0]
    wmat = {k: CI.material_share(_frame(_win(wcs, pos, *se))) for k, se in
            {"s7": (-7, 0), "b7": (-97, -7), "s14": (-14, 0), "b14": (-104, -14)}.items()}

    deaths = cm.pivot_table(index="month", columns="iso3", values="all_deaths", aggfunc="sum").sort_index()
    d12 = deaths.shift(1).rolling(12, min_periods=1).sum()          # months M-12..M-1
    fwd = {h: deaths[::-1].rolling(h, min_periods=h).sum()[::-1] for h in (3, 6)}  # months M..M+h-1
    sample_dates = days[np.clip(pos, 0, len(days) - 1)] if len(pos) else days[:0]
    months = sample_dates.to_period("M").to_timestamp()
    frames = []
    for iso, arr in news.items():
        if iso == "WLD" or iso not in deaths.columns:
            continue
        cs = _csum(arr)
        S = {name: _frame(_win(cs, pos, a, b)) for name, (a, b) in {
            "now30": (-30, 0), "norm": (-395, -30), "s7": (-7, 0), "b7": (-97, -7), "s14": (-14, 0),
            "b14": (-104, -14), "f3": (0, HORIZON_DAYS[3]), "f6": (0, HORIZON_DAYS[6])}.items()}
        sh = {k: CI.shares(v) for k, v in S.items()}
        ratio_now = _ratio(sh["now30"]["conflict"], sh["norm"]["conflict"]) / _ratio(W["now30"]["conflict"], W["norm"]["conflict"])
        dd12 = d12[iso].reindex(months).to_numpy()
        dims = CI.dimension_frame(sh["now30"], np.nan_to_num(dd12), ratio_now.to_numpy(), ref)
        row = pd.DataFrame({"iso3": iso, "date": sample_dates, "month": months})
        for k in CI.DIMENSIONS:
            row[f"idx_{k}"] = dims[k].to_numpy()
        row["idx_score"] = CI.compose(dims, weights).to_numpy()
        mat = {k: CI.material_share(S[k]) for k in ("s7", "b7", "s14", "b14")}
        row["st_surge7"] = (_ratio(mat["s7"], mat["b7"]) / _ratio(wmat["s7"], wmat["b7"])).to_numpy()
        row["st_surge14"] = (_ratio(mat["s14"], mat["b14"]) / _ratio(wmat["s14"], wmat["b14"])).to_numpy()
        row["st_tension14"] = (_ratio(sh["s14"]["tension"], sh["b14"]["tension"])
                               / _ratio(W["s14"]["tension"], W["b14"]["tension"])).to_numpy()
        row["st_protest14"] = (_ratio(sh["s14"]["unrest"], sh["b14"]["unrest"])
                               / _ratio(W["s14"]["unrest"], W["b14"]["unrest"])).to_numpy()
        ev14, evb14 = S["s14"]["ev"].to_numpy(), S["b14"]["ev"].to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            row["st_attention14"] = np.where((ev14 >= 100) & (evb14 > 0),
                                             (ev14 / 14) / (evb14 / 90) / ((wev14 / 14) / (wevb14 / 90)), np.nan)
        row["st_violent7"] = sh["s7"]["violent"].to_numpy()
        # future index (labels)
        for h in (3, 6):
            fdeaths = fwd[h][iso].reindex(months).to_numpy() * 12 / h
            fratio = _ratio(sh[f"f{h}"]["conflict"], sh["norm"]["conflict"]) / _ratio(W[f"f{h}"]["conflict"], W["norm"]["conflict"])
            fut = CI.dimension_frame(sh[f"f{h}"], np.nan_to_num(fdeaths), fratio.to_numpy(), ref)
            complete = (pos + HORIZON_DAYS[h] < len(days)) & ~np.isnan(fdeaths)
            row[f"idx_score_next_{h}m"] = np.where(complete, CI.compose(fut, weights).to_numpy(), np.nan)
        frames.append(row)
    out = pd.concat(frames, ignore_index=True)
    out["idx_score_chg_3m"] = out["idx_score"] - out.groupby("iso3")["idx_score"].shift(13)  # ~13 weeks earlier
    keep = [c for c in monthly.columns if not c.startswith(("idx_", "worse_", "better_", "can_rise_", "can_fall_"))]
    out = out.merge(monthly[keep], on=["iso3", "month"], how="left")
    return out


def add_labels(df: pd.DataFrame, cuts: dict[str, float]) -> pd.DataFrame:
    from app.ml.escalation import index_labels

    lab = index_labels(df.rename(columns={"date": "_date"}).assign(month=df["date"]), cuts)
    lab = lab.drop(columns=["idx_score_chg_3m"]).rename(columns={"month": "date"})
    return df.merge(lab, on=["iso3", "date"], how="left")
