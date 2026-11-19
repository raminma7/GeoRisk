"""Runtime settings: watchlist, thresholds, AI provider, feature flags (admin to change)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai.providers import provider_status
from app.core.config import get_settings
from app.core.db import get_db
from app.core.runtime_settings import EDITABLE, effective, set_value
from app.core.security import Principal, require_read, require_role
from app.ingestion.models import AuditLog

router = APIRouter(prefix="/settings", tags=["Settings"])


def _secret_state(v) -> str:
    return "configured" if v is not None and v.get_secret_value() else "not_configured"


@router.get("", summary="Effective settings (secrets are never returned)")
def get_all(s: Session = Depends(get_db), p: Principal = Depends(require_read)):
    cfg = get_settings()
    return {
        "editable": effective(s),
        "editable_keys": sorted(EDITABLE),
        "credentials": {"anthropic_api_key": _secret_state(cfg.anthropic_api_key),
                        "openai_api_key": _secret_state(cfg.openai_api_key),
                        "acled_api_key": _secret_state(cfg.acled_api_key),
                        "api_keys": "configured" if cfg.parsed_api_keys() else "not_configured"},
        "ai": provider_status(s),
        "feature_flags": {"economy": bool(effective(s)["feature_economy"]), "combined_analysis": cfg.feature_combined_analysis,
                          "acled": cfg.feature_acled},
        "schedules_minutes": {"gdelt_events": cfg.gdelt_events_interval_minutes, "gdelt_doc": cfg.gdelt_doc_interval_minutes,
                              "priority": cfg.priority_interval_minutes, "ai_crisis": cfg.ai_crisis_interval_minutes},
        "your_role": p.role,
        "note": "API keys are configured through environment variables only and are never exposed by the API.",
    }


class SettingUpdate(BaseModel):
    value: Any


@router.put("/{key}", summary="Update a runtime setting (admin)")
def update(key: str, body: SettingUpdate, s: Session = Depends(get_db), p: Principal = Depends(require_role("admin"))):
    if key not in EDITABLE:
        raise HTTPException(404, f"'{key}' is not an editable setting")
    if key == "ai_provider" and body.value not in ("none", "anthropic", "openai"):
        raise HTTPException(422, "ai_provider must be none|anthropic|openai")
    try:
        v = set_value(s, key, body.value, p.name)
    except TypeError as exc:
        raise HTTPException(422, str(exc)) from exc
    s.add(AuditLog(actor=p.name, action="setting_update", target=key, details={"value": v}))
    s.commit()
    return {"key": key, "value": v}
