"""Monthly predictions: "will this country's crisis get worse in the next 3 / 6 months?"

Build current factor rows -> ask the model service (crisis_escalation_3m / _6m) -> store.

The application never calls the model service while rendering pages; pages read ``ml_predictions``.
If the service is unavailable, the last stored predictions remain visible.
"""

from __future__ import annotations

import math

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.logging import get_logger
from app.ml.explain import reason
from app.ml.models import MlPrediction

log = get_logger(__name__)
MODELS = {3: "crisis_escalation_3m", 6: "crisis_escalation_6m"}
MODEL = MODELS[3]  # the primary model shown on the site
LEVELS = [(10.0, "very_high"), (4.0, "high"), (1.5, "elevated"), (0.0, "low")]


class ModelServiceUnavailable(Exception):
    pass


def _service(path: str, payload: dict | None = None):
    url = get_settings().model_service_url.rstrip("/") + path
    try:
        with httpx.Client(timeout=120) as c:
            r = c.post(url, json=payload) if payload is not None else c.get(url)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise ModelServiceUnavailable(f"{url}: {exc}") from exc


def model_info(name: str = MODEL) -> dict:
    items = _service("/models")["items"]
    m = next((i for i in items if i["name"] == name), None)
    if m is None:
        raise ModelServiceUnavailable(f"model '{name}' not loaded in model service")
    return m


def level_for(times_avg: float) -> str:
    return next(name for thr, name in LEVELS if times_avg >= thr)


def run_predictions(session: Session) -> dict:
    """Predict for every country with both models (3 and 6 months) and store the results."""
    from app.ml.escalation import TIER_NAMES, build_frame

    df = build_frame(session, forecast=True)
    prov = df.attrs.get("provenance", {})
    t0 = df["month"].max()
    rows = df[df["month"] == t0].copy()
    rows["tier_now"] = rows["tier_now"].fillna(0).astype(int)
    data_through = (t0 - pd.DateOffset(months=1)).date()
    infos, summary = {}, {}
    for h, name in MODELS.items():
        info = model_info(name)
        infos[name] = info
        summary[name] = _predict_one(session, name, info, rows, t0, h, data_through, TIER_NAMES)
    _store_model_info(session, infos)
    session.flush()
    return {"window_start": str(t0.date()), "data_through": str(data_through), "models": summary, "provenance": prov}


def _predict_one(session: Session, name: str, info: dict, rows: pd.DataFrame, t0, h: int, data_through, tiers) -> dict:
    feats: list[str] = info["features"]
    window_end = (t0 + pd.DateOffset(months=h - 1)).date()
    eligible = rows[rows["tier_now"] < len(tiers) - 1]          # already Critical: cannot get worse
    payload_rows = [{"id": r["iso3"], **{f: (None if pd.isna(r[f]) else float(r[f])) for f in feats}}
                    for _, r in eligible.iterrows()]
    preds, version = [], info["version"]
    for i in range(0, len(payload_rows), 500):
        out = _service("/predict", {"model": name, "rows": payload_rows[i:i + 500], "explain": True, "top_k": 12})
        preds.extend(out["predictions"])
        version = out["model_version"]
    tier_of = dict(zip(rows["iso3"], rows["tier_now"], strict=False))
    probs = np.array([p["probability"] for p in preds])
    raws = np.array([p["raw_score"] for p in preds])
    avg = float(probs.mean()) if len(probs) else 0.0
    order = np.argsort(-raws)  # rank by raw score (calibration plateaus would create ties)
    rank = np.empty(len(preds), dtype=int)
    rank[order] = np.arange(1, len(preds) + 1)
    now = utcnow()
    values = []
    for k, p in enumerate(preds):
        ta = p["probability"] / avg if avg > 0 else None
        values.append(dict(
            model_name=name, model_version=version, iso3=p["id"], window_start=t0.date(), window_end=window_end,
            data_through=data_through, status="predicted", current_tier=tiers[tier_of.get(p["id"], 0)],
            probability=p["probability"], raw_score=p["raw_score"], rank=int(rank[k]), out_of=len(preds),
            times_average=round(ta, 2) if ta is not None else None, level=level_for(ta or 0), created_at=now,
            # only explain with factors that have real data (a missing value is not a reason users can act on)
            reasons=[reason(c["factor"], c["value"], c["effect"]) for c in p.get("contributions", [])
                     if c["value"] is not None][:5],
        ))
    for iso in sorted(set(rows["iso3"]) - set(eligible["iso3"])):
        values.append(dict(model_name=name, model_version=version, iso3=iso, window_start=t0.date(), window_end=window_end,
                           data_through=data_through, status="top_tier", current_tier=tiers[-1], probability=None,
                           raw_score=None, rank=None, out_of=len(preds), times_average=None, level=None, reasons=[],
                           created_at=now))
    session.execute(delete(MlPrediction).where(MlPrediction.model_name == name, MlPrediction.model_version == version,
                                               MlPrediction.window_start == t0.date()))
    session.execute(insert(MlPrediction).values(values))
    session.flush()
    if name == MODEL:
        _watchlist_alerts(session, values)
    log.info("predictions_stored", model=name, version=version, window=str(t0.date()), predicted=len(preds))
    return {"model_version": version, "window": [str(t0.date()), str(window_end)], "predicted": len(preds),
            "already_critical": len(values) - len(preds)}


def _store_model_info(session: Session, infos: dict[str, dict]) -> None:
    """Snapshot of the served models' descriptions, so pages never need a live call to the model service."""
    from app.ingestion.models import AppSetting

    keep = {name: {k: info.get(k) for k in ("name", "version", "label_definition", "applies_to", "prediction_window",
                                            "trained_at", "test", "provenance", "disclaimer", "algorithm",
                                            "horizon_months", "top_features")}
            for name, info in infos.items()}
    row = session.get(AppSetting, "ml_model_info")
    if row is None:
        row = AppSetting(key="ml_model_info")
        session.add(row)
    row.value = keep
    row.updated_at = utcnow()
    row.updated_by = "predict-onsets"


def stored_model_info(session: Session, name: str = MODEL) -> dict:
    from app.ingestion.models import AppSetting

    row = session.get(AppSetting, "ml_model_info")
    value = row.value if row and isinstance(row.value, dict) else {}
    return value.get(name) or {}


def _watchlist_alerts(session: Session, values: list[dict]) -> None:
    """Info-level alert when a country enters the top 10 of 'could get worse in the next 3 months'."""
    from app.monitoring.models import GeopoliticalAlert
    from app.registry.models import Country

    names = dict(session.execute(select(Country.iso3, Country.name)).all())
    for v in values:
        if v["status"] != "predicted" or v["rank"] > 10 or v["level"] not in ("high", "very_high"):
            continue
        key = f"early_warning:{v['iso3']}:{v['window_start']}"
        why = "; ".join(f"{r['label']} {r['value']}" for r in v["reasons"][:3] if r["direction"] == "raises")
        session.execute(insert(GeopoliticalAlert).values(
            dedup_key=key, crisis_id=None, alert_type="early_warning", severity="info",
            title=f"Early warning: {names.get(v['iso3'], v['iso3'])} could get worse in the next 3 months",
            message=(f"Model estimate for {v['window_start']:%b}–{v['window_end']:%b %Y}: "
                     f"{v['times_average']:.1f}× the average risk of getting worse (rank {v['rank']}). Main factors: {why}."),
            evidence={"model": v["model_name"], "model_version": v["model_version"], "reasons": v["reasons"],
                      "caveat": "Research estimate based on historical patterns, not a forecast of events."},
            status="open", requires_review=False, notified=True, created_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=["dedup_key"]))


# ------------------------------------------------------------------ reads
def latest_window(session: Session, name: str = MODEL) -> tuple[str, object] | None:
    row = session.execute(select(MlPrediction.model_version, MlPrediction.window_start)
                          .where(MlPrediction.model_name == name)
                          .order_by(desc(MlPrediction.created_at)).limit(1)).first()
    return (row[0], row[1]) if row else None


def ser(p: MlPrediction, name: str | None = None) -> dict:
    return {"iso3": p.iso3, "name": name, "status": p.status, "current_tier": p.current_tier,
            "probability": p.probability, "rank": p.rank,
            "out_of": p.out_of, "times_average": p.times_average, "level": p.level, "reasons": p.reasons,
            "window": {"start": p.window_start.isoformat(), "end": p.window_end.isoformat()},
            "data_through": p.data_through.isoformat(), "model_version": p.model_version}


def history(session: Session, iso3: str, limit: int = 24, name: str = MODEL) -> list[dict]:
    rows = session.scalars(select(MlPrediction).where(MlPrediction.model_name == name, MlPrediction.iso3 == iso3)
                           .order_by(desc(MlPrediction.window_start), desc(MlPrediction.created_at)).limit(limit * 3))
    seen, out = set(), []
    for r in rows:
        if r.window_start in seen:
            continue
        seen.add(r.window_start)
        out.append({"window_start": r.window_start.isoformat(), "probability": r.probability,
                    "times_average": r.times_average, "rank": r.rank, "status": r.status})
    return list(reversed(out[:limit]))


def count_by_level(session: Session, version: str, window, name: str = MODEL) -> dict:
    return dict(session.execute(select(MlPrediction.level, func.count()).where(
        MlPrediction.model_name == name, MlPrediction.model_version == version, MlPrediction.window_start == window, MlPrediction.status == "predicted")
        .group_by(MlPrediction.level)).all())


def _isnan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


# --------------------------------------------------------------- first-run setup
STATUS_KEY = "ml_bootstrap_status"
STEPS = ["ucdp", "factors", "news", "model_service", "predict"]
STEP_LABELS = {"ucdp": "Importing UCDP conflict history", "factors": "Downloading country factors (World Bank, V-Dem, …)",
               "news": "Loading recent news archive", "model_service": "Connecting to the model service",
               "predict": "Calculating predictions"}


def _set_status(**fields) -> None:
    """Persist setup progress immediately (own transaction) so the UI can show it while the job runs."""
    from app.core.db import session_scope
    from app.ingestion.models import AppSetting

    with session_scope() as s:
        row = s.get(AppSetting, STATUS_KEY)
        if row is None:
            row = AppSetting(key=STATUS_KEY, value={})
            s.add(row)
        row.value = {**(row.value or {}), **fields, "updated_at": utcnow().isoformat()}
        row.updated_by = "early-warning-setup"


def setup_status(session: Session) -> dict | None:
    from app.ingestion.models import AppSetting

    row = session.get(AppSetting, STATUS_KEY)
    return row.value if row else None


def bootstrap_early_warning(news_years: int = 2, wait_for_service_s: int = 300) -> dict:
    """Make predictions available on a fresh install, using the model bundled with the model service.

    Only recent news (``news_years``) is needed for current predictions; the full 1988→ archive is only needed
    to retrain (``python -m app.cli backfill-news-history`` + ``train-escalation-models``).
    """
    import time
    from datetime import date

    from app.core.db import session_scope
    from app.ingestion.connectors.ucdp import UcdpGedConnector, latest_candidate, latest_version
    from app.ml.factors import download_factors, missing
    from app.ml.news_history import backfill

    out: dict = {}
    _set_status(state="running", step="ucdp", step_label=STEP_LABELS["ucdp"], started_at=utcnow().isoformat(), error=None,
                 progress=None)
    try:
        with session_scope() as s:
            con = UcdpGedConnector(s)
            out["ucdp"] = "present" if latest_version(s) else con.import_version()
            if latest_candidate(s) is None:
                try:
                    out["ucdp_candidate"] = con.import_latest_candidate()
                except Exception as exc:  # noqa: BLE001 - preliminary data is optional
                    out["ucdp_candidate"] = f"skipped: {exc}"[:200]

        _set_status(step="factors", step_label=STEP_LABELS["factors"])
        out["factors"] = download_factors() if missing() else {"status": "present"}

        _set_status(step="news", step_label=STEP_LABELS["news"], progress={"done": 0, "total": None})
        y = date.today().year
        out["news"] = backfill(y - news_years + 1, y, workers=2,
                               on_progress=lambda d, t: _set_status(progress={"done": d, "total": t}) if d % 25 == 0 else None)

        _set_status(step="model_service", step_label=STEP_LABELS["model_service"], progress=None)
        deadline = time.monotonic() + wait_for_service_s
        while True:
            try:
                model_info()
                break
            except ModelServiceUnavailable:
                if time.monotonic() > deadline:
                    raise
                time.sleep(10)

        _set_status(step="predict", step_label=STEP_LABELS["predict"])
        with session_scope() as s:
            out["predictions"] = run_predictions(s)
        _set_status(state="done", step=None, step_label=None, finished_at=utcnow().isoformat())
        return out
    except Exception as exc:
        _set_status(state="failed", error=f"{type(exc).__name__}: {exc}"[:500])
        log.error("early_warning_setup_failed", error=str(exc))
        raise


def needs_bootstrap(session: Session) -> bool:
    if latest_window(session) is not None:
        return False
    st = setup_status(session) or {}
    if st.get("state") == "running":
        # treat a run with no progress for 2 hours as dead (e.g. container restart)
        from datetime import datetime, timedelta

        try:
            return utcnow() - datetime.fromisoformat(st.get("updated_at")) > timedelta(hours=2)
        except (TypeError, ValueError):
            return True
    return True
