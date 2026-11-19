"""AI analysis endpoints (multi-country, on-demand runs)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.models import GeopoliticalAnalysis
from app.ai.providers import provider_status
from app.api.v1.common import get_crisis, iso, parse_countries
from app.core.config import get_settings
from app.core.db import get_db
from app.core.ratelimit import limiter
from app.core.security import Principal, require_read, require_role

router = APIRouter(prefix="/analysis", tags=["AI Analysis"])


def analysis_response(a: GeopoliticalAnalysis | None, session: Session | None = None) -> dict:
    status = provider_status(session)
    if a is None:
        return {"status": "unavailable" if not status["configured"] else "not_generated",
                "ai_provider": status, "analysis": None,
                "message": ("No AI provider is configured; the platform continues to work without AI. "
                            "Set AI_PROVIDER and the matching API key to enable analyses.")
                if not status["configured"] else "No analysis has been generated for this scope yet."}
    return {
        "status": a.status, "ai_provider": status,
        "analysis": {"id": a.id, "scope_type": a.scope_type, "scope_key": a.scope_key, "mode": a.mode,
                     "provider": a.provider, "model": a.model, "prompt_version": a.prompt_version,
                     "created_at": iso(a.created_at), "trigger": a.trigger, "evidence_ids": a.evidence_ids,
                     "output": a.output, "error": a.error, "input_tokens": a.input_tokens,
                     "output_tokens": a.output_tokens},
    }


class MultiCountryRequest(BaseModel):
    countries: list[str] = Field(..., min_length=1, max_length=25)
    mode: Literal["geopolitical", "economic", "combined"] = "geopolitical"
    force: bool = False


@router.post("/multi-country", summary="AI analysis of any number of countries (requires analyst role)")
@limiter.limit(get_settings().rate_limit_expensive)
def multi_country(request: Request, body: MultiCountryRequest, s: Session = Depends(get_db),
                  _: Principal = Depends(require_role("analyst"))):
    from app.ai.service import analyze_countries

    if body.mode != "geopolitical":
        raise HTTPException(501, {"status": "planned", "message": "Economic and combined modes are disabled until the "
                                                                  "Economic Intelligence module is implemented."})
    isos = [c.iso3 for c in parse_countries(s, body.countries)]
    a = analyze_countries(s, isos, trigger="on_demand", force=body.force)
    s.commit()
    return analysis_response(a, s)


@router.post("/crises/{crisis_id}", summary="Run crisis analysis now (analyst)")
@limiter.limit(get_settings().rate_limit_expensive)
def run_crisis(request: Request, crisis_id: str, force: bool = False, s: Session = Depends(get_db),
               _: Principal = Depends(require_role("analyst"))):
    from app.ai.service import analyze_crisis

    c = get_crisis(s, crisis_id)
    a = analyze_crisis(s, c.id, trigger="on_demand", force=force)
    s.commit()
    return analysis_response(a, s)


@router.post("/countries/{code}", summary="Run country analysis now (analyst)")
@limiter.limit(get_settings().rate_limit_expensive)
def run_country(request: Request, code: str, force: bool = False, s: Session = Depends(get_db),
                _: Principal = Depends(require_role("analyst"))):
    from app.ai.service import analyze_countries

    c = parse_countries(s, [code])[0]
    a = analyze_countries(s, [c.iso3], trigger="on_demand", force=force)
    s.commit()
    return analysis_response(a, s)


@router.get("/reports", summary="Latest intelligence reports and analyses")
def reports(scope_type: str | None = None, limit: int = 20, s: Session = Depends(get_db),
            _: Principal = Depends(require_read)):
    q = select(GeopoliticalAnalysis).order_by(desc(GeopoliticalAnalysis.created_at)).limit(min(limit, 100))
    if scope_type:
        q = q.where(GeopoliticalAnalysis.scope_type == scope_type)
    return {"ai_provider": provider_status(s), "items": [analysis_response(a, s)["analysis"] for a in s.scalars(q)]}
