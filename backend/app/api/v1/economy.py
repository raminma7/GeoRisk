"""Economic Intelligence API — planned module (feature-flagged).

Endpoints exist so clients and navigation are stable, but return
HTTP 501 with ``status: planned``. No economic statistics are served or
fabricated. Indicator *definitions* (metadata only) are available.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.runtime_settings import get_value
from app.core.security import Principal, require_read
from app.economy.connectors import CONNECTORS, INDICATORS

router = APIRouter(prefix="/economy", tags=["Economy (planned)"])


def _planned(s: Session, what: str) -> JSONResponse:
    return JSONResponse(status_code=501, content={
        "status": "planned", "feature_flag": {"feature_economy": bool(get_value(s, "feature_economy"))},
        "message": f"{what} is part of the Economic Intelligence module (Phase 5+) and is not implemented yet. "
                   "No economic statistics are available.",
        "connectors": {k: {"requires_credentials": c.requires_credentials, "release_schedule": c.default_release_schedule}
                       for k, c in CONNECTORS.items()},
    })


@router.get("/global", status_code=501)
def global_economy(s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    return _planned(s, "Global economic intelligence")


@router.get("/countries/{code}", status_code=501)
def country_economy(code: str, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    return _planned(s, "Country economic intelligence")


@router.get("/indicators", summary="Planned indicator definitions (metadata only, no values)")
def indicators(s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    return {"status": "planned", "values_available": False,
            "items": [i.__dict__ for i in INDICATORS],
            "note": "Definitions only. Units, frequencies and aggregation methods are recorded so that "
                    "incompatible statistics are never combined (e.g. inflation rates are never summed)."}


class EconCompare(BaseModel):
    countries: list[str] = Field(..., min_length=1, max_length=25)
    indicators: list[str] = Field(default_factory=list)


@router.post("/compare", status_code=501)
def compare(body: EconCompare, s: Session = Depends(get_db), _: Principal = Depends(require_read)):
    return _planned(s, "Economic comparison")
