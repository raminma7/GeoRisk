"""Explainable monitoring priorities and evidence-based alerts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class MonitoringPriority(Base):
    """One scoring run for one crisis. Review priority — NOT a probability of war."""

    __tablename__ = "monitoring_priorities"
    __table_args__ = (Index("ix_mon_prio_crisis_time", "crisis_id", "computed_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    crisis_id: Mapped[int] = mapped_column(ForeignKey("crises.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(20))
    components: Mapped[dict] = mapped_column(JSONB)  # each component: value, weight, contribution, inputs
    triggering_indicators: Mapped[list] = mapped_column(JSONB, default=list)
    supporting_event_cluster_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_confidence: Mapped[float] = mapped_column(Float)
    evidence_confidence_label: Mapped[str] = mapped_column(String(20))
    explanation: Mapped[str] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(String(20))
    weights: Mapped[dict] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(default=utcnow)


class GeopoliticalAlert(Base):
    __tablename__ = "geopolitical_alerts"
    __table_args__ = (Index("ix_alerts_created", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    crisis_id: Mapped[int | None] = mapped_column(ForeignKey("crises.id", ondelete="CASCADE"), index=True)
    priority_id: Mapped[int | None] = mapped_column(ForeignKey("monitoring_priorities.id", ondelete="SET NULL"))
    alert_type: Mapped[str] = mapped_column(String(40))  # priority_threshold | escalation | de_escalation | new_crisis | ingestion_failure
    severity: Mapped[str] = mapped_column(String(20))  # info | elevated | high | critical
    title: Mapped[str] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | acknowledged | dismissed
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None]
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
