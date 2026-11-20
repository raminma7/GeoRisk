"""GeoRisk API application."""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.api.v1 import analysis, auth, countries, crises, datasets, economy, monitoring, news, predictions
from app.api.v1 import settings as settings_api
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.ratelimit import limiter

configure_logging()
log = get_logger("api")
cfg = get_settings()

DESCRIPTION = """
**GeoRisk** — automated global geopolitical & conflict monitoring (Economic Intelligence module planned).

* Events are machine-extracted from news (GDELT) and are **not independently verified**.
* Monitoring priority scores express **analyst review priority**, not probabilities of war.
* AI analyses are **interpretations**, stored with provider, model, prompt version and evidence IDs.
* Read endpoints are public when `PUBLIC_READ=true`; write endpoints need an `X-API-Key` with the right role.
"""

app = FastAPI(title="GeoRisk API", version="0.1.0", description=DESCRIPTION,
              openapi_tags=[{"name": "Global Monitoring"}, {"name": "Countries"}, {"name": "Crises"},
                            {"name": "News and Events"}, {"name": "AI Analysis"}, {"name": "Datasets & Models"},
                            {"name": "Early warning (experimental ML)"}, {"name": "Economy (planned)"}, {"name": "Settings"}, {"name": "Health"}])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_origin_regex=cfg.cors_origin_regex or None,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_logging(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    t0 = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request_failed", path=request.url.path, request_id=rid)
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": rid})
    response.headers["x-request-id"] = rid
    if request.url.path not in ("/health",):
        log.info("request", method=request.method, path=request.url.path, status=response.status_code,
                 ms=round((time.monotonic() - t0) * 1000, 1), request_id=rid)
    return response


v1 = APIRouter(prefix="/api/v1")
for r in (monitoring.router, countries.router, crises.router, news.router, analysis.router, datasets.router,
          predictions.router, economy.router, settings_api.router, auth.router):
    v1.include_router(r)
app.include_router(v1)


@app.get("/health", tags=["Health"], summary="Liveness")
def health():
    return {"status": "ok"}


@app.get("/ready", tags=["Health"], summary="Readiness: database and Redis reachable")
def ready():
    out = {"database": False, "redis": False}
    try:
        with SessionLocal() as s:
            s.execute(text("SELECT 1"))
        out["database"] = True
    except Exception as exc:  # noqa: BLE001
        out["database_error"] = type(exc).__name__
    try:
        from app.workers.locks import redis_client

        out["redis"] = bool(redis_client().ping())
    except Exception as exc:  # noqa: BLE001
        out["redis_error"] = type(exc).__name__
    ok = out["database"]
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", **out})
