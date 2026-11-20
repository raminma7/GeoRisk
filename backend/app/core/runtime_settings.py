"""Runtime-editable settings layered over environment configuration.

Only keys in ``EDITABLE`` can be changed through the Settings API. Secrets are
never stored here — API keys stay in the environment.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.ingestion.models import AppSetting

EDITABLE = {
    "watchlist": list,
    "priority_weights": dict,
    "priority_levels": dict,
    "crisis_index_weights": dict,
    "alert_threshold_level": str,
    "crisis_attach_threshold": float,
    "crisis_review_threshold": float,
    "crisis_min_event_clusters": int,
    "crisis_min_sources": int,
    "gdelt_doc_topic_queries": dict,
    "ai_provider": str,
    "ai_max_crises_per_run": int,
    "feature_economy": bool,
}


def get_value(session: Session | None, key: str) -> Any:
    default = getattr(get_settings(), key)
    if session is None or key not in EDITABLE:
        return default
    row = session.get(AppSetting, key)
    return default if row is None or row.value is None else row.value


def set_value(session: Session, key: str, value: Any, user: str) -> Any:
    if key not in EDITABLE:
        raise KeyError(key)
    typ = EDITABLE[key]
    if typ is float and isinstance(value, int):
        value = float(value)
    if not isinstance(value, typ):
        raise TypeError(f"{key} must be {typ.__name__}")
    if key == "watchlist":
        value = sorted({str(v).upper()[:3] for v in value})
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        session.add(row)
    row.value = value
    row.updated_at = utcnow()
    row.updated_by = user
    return value


def effective(session: Session | None) -> dict[str, Any]:
    return {k: get_value(session, k) for k in EDITABLE}
