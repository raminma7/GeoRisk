"""Early-warning predictions (experimental ML model, served by the separate model service and stored monthly)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.v1.common import get_country, get_crisis, iso, region_iso3s
from app.core.db import get_db
from app.core.security import Principal, require_read
from app.ml import predictions as P
from app.ml.models import MlPrediction
from app.registry.models import Country, Region

router = APIRouter(prefix="/predictions", tags=["Early warning (experimental ML)"])

EXPLAINER = {
    "question": "Will a new conflict start? (at least 25 deaths within the 3-month window, in a country with no "
                "conflict deaths in the previous 2 years)",
    "how": "A machine-learning model trained on 35 years of verified conflict data (UCDP) and on each country's "
           "neighbours, politics, economy and news trends.",
    "levels": {"very_high": "≥10× the average country", "high": "4–10× average", "elevated": "1.5–4× average",
               "low": "below 1.5× average"},
    "caveat": "Experimental research estimate. New conflicts are rare: even high-risk countries usually stay peaceful. "
              "This is a watch list for analysts, not a forecast of war.",
}


def _meta(session: Session) -> dict | None:
    lw = P.latest_window(session)
    if not lw:
        return None
    version, window = lw
    first = session.scalar(select(MlPrediction).where(MlPrediction.model_version == version,
                                                      MlPrediction.window_start == window).limit(1))
    info = P.stored_model_info(session)  # snapshot taken at prediction time: no live model-service call
    t = info.get("test") or {}
    quality = {"roc_auc": t.get("roc_auc"), "top20_share_caught": t.get("top20_share_caught"),
               "test_years": t.get("years"), "onsets_tested": t.get("onsets_tested")} if t else None
    return {"model_version": version, "window": {"start": iso(first.window_start), "end": iso(first.window_end)},
            "data_through": iso(first.data_through), "generated_at": iso(first.created_at), "test_quality": quality,
            "trained_at": info.get("trained_at"), "levels": P.count_by_level(session, version, window), "explainer": EXPLAINER}


def latest_for(session: Session, iso3: str) -> MlPrediction | None:
    lw = P.latest_window(session)
    if not lw:
        return None
    return session.scalar(select(MlPrediction).where(MlPrediction.model_version == lw[0], MlPrediction.window_start == lw[1],
                                                     MlPrediction.iso3 == iso3))


def country_risk(session: Session, iso3: str) -> dict | None:
    p = latest_for(session, iso3)
    if p is None:
        return None
    name = session.scalar(select(Country.name).where(Country.iso3 == iso3))
    return {**P.ser(p, name), "history": P.history(session, iso3), "explainer": EXPLAINER}


@router.get("", summary="Early-warning watch list: countries ranked by modelled risk of new conflict")
def watch_list(limit: int = Query(50, ge=1, le=300), region: str | None = None, level: str | None = None,
               s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    meta = _meta(s)
    if meta is None:
        return {"meta": None, "items": [], "ongoing_conflict": [],
                "message": "No predictions yet. Run `python -m app.cli predict-onsets` (needs the model service)."}
    lw = P.latest_window(s)
    q = (select(MlPrediction, Country.name, Region.name)
         .join(Country, Country.iso3 == MlPrediction.iso3).outerjoin(Region, Region.id == Country.subregion_id)
         .where(MlPrediction.model_version == lw[0], MlPrediction.window_start == lw[1]))
    if region:
        q = q.where(MlPrediction.iso3.in_(region_iso3s(s, region)))
    rows = s.execute(q).all()
    predicted = sorted((r for r in rows if r[0].status == "predicted"), key=lambda r: r[0].rank)
    if level:
        predicted = [r for r in predicted if r[0].level == level]
    ongoing = sorted((r for r in rows if r[0].status == "ongoing_conflict"), key=lambda r: r[1])
    return {"meta": meta, "items": [{**P.ser(p, n), "region": rg} for p, n, rg in predicted[:limit]],
            "ongoing_conflict": [{"iso3": p.iso3, "name": n} for p, n, _ in ongoing], "total_predicted": len(predicted)}


@router.get("/countries/{code}", summary="Model risk for one country, with monthly history")
def one_country(code: str, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    c = get_country(s, code)
    r = country_risk(s, c.iso3)
    if r is None:
        raise HTTPException(404, "No prediction for this country yet")
    return r


@router.get("/crises/{crisis_id}/spillover", summary="Model risk for neighbours not (yet) involved in a crisis")
def spillover(crisis_id: str, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    return {"items": crisis_spillover(s, crisis_id)}


def crisis_spillover(s: Session, crisis_id: str) -> list[dict]:
    from app.ml.enrichment import neighbours_iso3

    c = get_crisis(s, crisis_id)
    involved = set(c.core_country_iso3s or []) | set(c.location_country_iso3s or [])
    nb = neighbours_iso3(s)
    cands = sorted({n for i in involved for n in nb.get(i, [])} - involved)
    lw = P.latest_window(s)
    if not lw or not cands:
        return []
    rows = s.execute(select(MlPrediction, Country.name).join(Country, Country.iso3 == MlPrediction.iso3)
                     .where(MlPrediction.model_version == lw[0], MlPrediction.window_start == lw[1],
                            MlPrediction.iso3.in_(cands))).all()
    out = []
    for p, name in rows:
        borders = sorted(i for i in involved if p.iso3 in nb.get(i, []))
        out.append({**P.ser(p, name), "borders": borders})
    return sorted(out, key=lambda x: (x["status"] != "predicted", -(x["probability"] or 0)))


_ = or_  # keep import for future filters
