from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class GeopoliticalAnalysis(Base):
    """Stored AI analysis. AI output is an *interpretation*, never ground truth."""

    __tablename__ = "geopolitical_analyses"
    __table_args__ = (Index("ix_geo_analyses_scope", "scope_type", "scope_key", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(30))  # crisis | country | multi_country | daily_report
    scope_key: Mapped[str] = mapped_column(String(300))
    analysis_type: Mapped[str] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(30), default="geopolitical")  # geopolitical | economic | combined
    status: Mapped[str] = mapped_column(String(20))  # success | failed | invalid_output
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(20))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    evidence_ids: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"event_clusters": [...], "articles": [...]}
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(30), default="scheduled")  # scheduled | event | on_demand
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
