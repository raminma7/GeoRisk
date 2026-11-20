"""Early warning: "will this country's crisis get worse in the next 3 / 6 months?" (ML models served by the separate
model service; predictions are stored monthly)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.common import get_country, get_crisis, iso, region_iso3s
from app.core.db import get_db
from app.core.security import Principal, require_read
from app.ml import predictions as P
from app.ml.models import MlPrediction
from app.registry.models import Country, Region

router = APIRouter(prefix="/predictions", tags=["Early warning (ML)"])

EXPLAINER = {
    "question": "Will this country's situation get worse in the next 3 months, i.e. move up at least one severity "
                "level (Low → Moderate → High → Critical, by deaths from organised violence)? For a calm country "
                "that means a new crisis; for a country in crisis, escalation.",
    "how": "A machine-learning model trained on 35 years of verified conflict data (UCDP, all kinds of organised "
           "violence) and on each country's recent trend, neighbours, politics, economy and news. A second model "
           "looks 6 months ahead.",
    "levels": {"very_high": "≥10× the average country", "high": "4–10× average", "elevated": "1.5–4× average",
               "low": "below 1.5× average"},
    "caveat": "Research estimate based on historical patterns. Getting worse is rare: even high-risk countries "
              "usually stay as they are. Countries already at Critical are not scored.",
}


def _meta(session: Session) -> dict | None:
    lw = P.latest_window(session)
    six = P.latest_window(session, P.MODELS[6])
    if not lw:
        return None
    version, window = lw
    first = session.scalar(select(MlPrediction).where(MlPrediction.model_name == P.MODEL, MlPrediction.model_version == version,
                                                      MlPrediction.window_start == window).limit(1))
    info = P.stored_model_info(session)  # snapshot taken at prediction time: no live model-service call
    t = info.get("test") or {}
    m = t.get("model") or {}
    quality = {"roc_auc": m.get("roc_auc"), "top20_share_caught": m.get("top20_caught"), "test_years": t.get("years"),
               "cases_tested": m.get("positives"),
               "simple_rule_roc_auc": (t.get("rule_deaths_trend") or {}).get("roc_auc")} if m else None
    six_window = None
    if six:
        s6 = session.scalar(select(MlPrediction).where(MlPrediction.model_name == P.MODELS[6],
                                                       MlPrediction.model_version == six[0],
                                                       MlPrediction.window_start == six[1]).limit(1))
        six_window = {"start": iso(s6.window_start), "end": iso(s6.window_end)} if s6 else None
    return {"model_version": version, "window": {"start": iso(first.window_start), "end": iso(first.window_end)},
            "data_through": iso(first.data_through), "generated_at": iso(first.created_at), "test_quality": quality,
            "trained_at": info.get("trained_at"), "levels": P.count_by_level(session, version, window),
            "window_6m": six_window, "explainer": EXPLAINER}


def latest_for(session: Session, iso3: str, model: str = P.MODEL) -> MlPrediction | None:
    lw = P.latest_window(session, model)
    if not lw:
        return None
    return session.scalar(select(MlPrediction).where(MlPrediction.model_name == model, MlPrediction.model_version == lw[0],
                                                     MlPrediction.window_start == lw[1], MlPrediction.iso3 == iso3))


def six_months(session: Session) -> dict[str, MlPrediction]:
    lw = P.latest_window(session, P.MODELS[6])
    if not lw:
        return {}
    return {p.iso3: p for p in session.scalars(select(MlPrediction).where(
        MlPrediction.model_name == P.MODELS[6], MlPrediction.model_version == lw[0], MlPrediction.window_start == lw[1]))}


def _six(p: MlPrediction | None) -> dict | None:
    if p is None:
        return None
    return {"probability": p.probability, "times_average": p.times_average, "level": p.level, "status": p.status,
            "window": {"start": iso(p.window_start), "end": iso(p.window_end)}}


def country_risk(session: Session, iso3: str) -> dict | None:
    p = latest_for(session, iso3)
    if p is None:
        return None
    name = session.scalar(select(Country.name).where(Country.iso3 == iso3))
    return {**P.ser(p, name), "six_months": _six(latest_for(session, iso3, P.MODELS[6])),
            "history": P.history(session, iso3), "explainer": EXPLAINER}


@router.get("", summary="Countries ranked by modelled risk of getting worse in the next 3 months")
def watch_list(limit: int = Query(50, ge=1, le=300), region: str | None = None, level: str | None = None,
               group: str | None = Query(None, pattern="^(calm|crisis)$",
                                         description="calm = currently Low (risk of a new crisis); crisis = Moderate/High"),
               s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    meta = _meta(s)
    if meta is None:
        return {"meta": None, "items": [], "already_critical": [], "setup": P.setup_status(s),
                "setup_steps": [{"id": k, "label": P.STEP_LABELS[k]} for k in P.STEPS],
                "message": "The early-warning model is being set up. In Docker this starts automatically; manually: "
                           "`python -m app.cli setup-early-warning`."}
    lw = P.latest_window(s)
    q = (select(MlPrediction, Country.name, Region.name)
         .join(Country, Country.iso3 == MlPrediction.iso3).outerjoin(Region, Region.id == Country.subregion_id)
         .where(MlPrediction.model_name == P.MODEL, MlPrediction.model_version == lw[0], MlPrediction.window_start == lw[1]))
    if region:
        q = q.where(MlPrediction.iso3.in_(region_iso3s(s, region)))
    rows = s.execute(q).all()
    predicted = sorted((r for r in rows if r[0].status == "predicted"), key=lambda r: r[0].rank)
    if level:
        predicted = [r for r in predicted if r[0].level == level]
    if group == "calm":
        predicted = [r for r in predicted if r[0].current_tier == "low"]
    elif group == "crisis":
        predicted = [r for r in predicted if r[0].current_tier in ("elevated", "high")]
    top = sorted((r for r in rows if r[0].status == "top_tier"), key=lambda r: r[1])
    six = six_months(s)
    return {"meta": meta,
            "items": [{**P.ser(p, n), "region": rg, "six_months": _six(six.get(p.iso3))} for p, n, rg in predicted[:limit]],
            "already_critical": [{"iso3": p.iso3, "name": n} for p, n, _ in top], "total_predicted": len(predicted)}


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
                     .where(MlPrediction.model_name == P.MODEL, MlPrediction.model_version == lw[0],
                            MlPrediction.window_start == lw[1], MlPrediction.iso3.in_(cands))).all()
    out = []
    for p, name in rows:
        borders = sorted(i for i in involved if p.iso3 in nb.get(i, []))
        out.append({**P.ser(p, name), "borders": borders})
    return sorted(out, key=lambda x: (x["status"] != "predicted", -(x["probability"] or 0)))  # most at risk first

