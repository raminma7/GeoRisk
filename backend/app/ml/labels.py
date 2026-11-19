"""Independently defined outcome labels derived from UCDP GED.

Labels never come from LLM output or from GDELT-derived monitoring scores.
Definitions (``ucdp-v1``), per country and calendar month ``t`` with horizon
``h`` months (window ``[t, t+h)``):

* ``state_based_event`` — 1 if UCDP records >=1 state-based (type 1) event
  in the country in the window. (UCDP: state-based armed conflict, >=25
  battle-related deaths per calendar year at the conflict level.)
* ``interstate_event`` — 1 if any type-1 event in the window has a
  government on both sides (``gwnoa`` and ``gwnob`` both set).
* ``state_based_fatalities`` — sum of ``best`` fatality estimates, type 1
  (continuous; "changes in recorded conflict intensity").
* ``escalation`` — 1 if type-1 fatalities in the window are >= 2x the
  preceding window of the same length AND >= 25, given the preceding window
  had >= 1 fatality (escalation of an already documented conflict).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ingestion.connectors.ucdp import latest_version
from app.ml.models import OutcomeLabel

LABEL_VERSION = "ucdp-v1"


def ucdp_country_months(session: Session, dataset_version_id: int) -> pd.DataFrame:
    """Monthly UCDP aggregates per country (all months between first and last record, zeros filled)."""
    rows = session.execute(text(
        "SELECT c.iso3, date_trunc('month', u.date_start)::date AS month, "
        " count(*) FILTER (WHERE u.type_of_violence=1) AS sb_events, "
        " coalesce(sum(u.deaths_best) FILTER (WHERE u.type_of_violence=1),0) AS sb_deaths, "
        " count(*) FILTER (WHERE u.type_of_violence=1 AND u.is_interstate) AS interstate_events, "
        " count(*) FILTER (WHERE u.type_of_violence=2) AS ns_events, "
        " count(*) FILTER (WHERE u.type_of_violence=3) AS os_events, "
        " coalesce(sum(u.deaths_best),0) AS all_deaths "
        "FROM ucdp_events u JOIN countries c ON c.id = u.country_id WHERE u.dataset_version_id = :dv "
        "GROUP BY 1, 2"), {"dv": dataset_version_id}).all()
    df = pd.DataFrame(rows, columns=["iso3", "month", "sb_events", "sb_deaths", "interstate_events", "ns_events",
                                     "os_events", "all_deaths"])
    if df.empty:
        return df
    df["month"] = pd.to_datetime(df["month"])
    months = pd.date_range(df["month"].min(), df["month"].max(), freq="MS")
    idx = pd.MultiIndex.from_product([sorted(df["iso3"].unique()), months], names=["iso3", "month"])
    return df.set_index(["iso3", "month"]).reindex(idx, fill_value=0).reset_index()


def compute_labels(cm: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """Forward-looking labels for each (country, month). Months whose window runs past the data end are dropped."""
    out = []
    for iso, g in cm.sort_values("month").groupby("iso3"):
        g = g.reset_index(drop=True)
        fwd = lambda col: g[col][::-1].rolling(horizon, min_periods=horizon).sum()[::-1]  # noqa: E731 - [t, t+h)
        sb = fwd("sb_events")
        inter = fwd("interstate_events")
        deaths = fwd("sb_deaths")
        prev = g["sb_deaths"].rolling(horizon, min_periods=horizon).sum().shift(1)
        lab = pd.DataFrame({
            "iso3": iso, "month": g["month"],
            "state_based_event": (sb >= 1).astype(float).where(sb.notna()),
            "interstate_event": (inter >= 1).astype(float).where(inter.notna()),
            "state_based_fatalities": deaths,
            "escalation": ((deaths >= 2 * prev) & (deaths >= 25)).astype(float).where(prev.notna() & deaths.notna() & (prev >= 1)),
        })
        out.append(lab)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def store_labels(session: Session, horizon: int = 1, start: date | None = None) -> dict:
    dv = latest_version(session)
    if dv is None:
        return {"status": "no_ucdp_dataset"}
    cm = ucdp_country_months(session, dv.id)
    labs = compute_labels(cm, horizon)
    if start is not None:
        labs = labs[labs["month"] >= pd.Timestamp(start)]
    n = 0
    rows = []
    for r in labs.itertuples(index=False):
        ws = r.month.date()
        we = (r.month + pd.DateOffset(months=horizon)).date()
        for name in ("state_based_event", "interstate_event", "state_based_fatalities", "escalation"):
            v = getattr(r, name)
            if pd.isna(v):
                continue
            rows.append(dict(entity_type="country", entity_key=r.iso3, label_name=f"{name}_next_{horizon}m",
                             label_version=LABEL_VERSION, window_start=ws, window_end=we, value=float(v),
                             source_dataset_version_id=dv.id))
        if len(rows) >= 4000:
            n += _flush(session, rows)
            rows = []
    if rows:
        n += _flush(session, rows)
    return {"status": "ok", "dataset_version": dv.version, "labels_inserted": n, "horizon_months": horizon}


def _flush(session: Session, rows: list[dict]) -> int:
    res = session.execute(insert(OutcomeLabel).values(rows).on_conflict_do_nothing().returning(OutcomeLabel.id))
    return len(res.all())
