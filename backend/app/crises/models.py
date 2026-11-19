"""First-class crisis entity and its evidence trail."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow

CRISIS_STATUSES = ["candidate", "active", "dormant", "resolved", "rejected", "merged"]


class Crisis(Base):
    __tablename__ = "crises"
    __table_args__ = (Index("ix_crises_status_priority", "status", "current_priority"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(40), unique=True)  # stable public identifier "CR-2026-000123"
    title: Mapped[str] = mapped_column(String(300))
    title_source: Mapped[str] = mapped_column(String(20), default="rule")  # rule | ai | human
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    crisis_type: Mapped[str] = mapped_column(String(40), default="mixed")  # armed_conflict | diplomatic | unrest | mixed
    region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"))
    core_country_iso3s: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    location_country_iso3s: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    first_observed_at: Mapped[datetime]
    last_event_at: Mapped[datetime]
    last_updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    verification_status: Mapped[str] = mapped_column(String(30), default="machine_extracted")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    current_priority: Mapped[float] = mapped_column(Float, default=0.0)
    priority_level: Mapped[str] = mapped_column(String(20), default="low")
    direction: Mapped[str] = mapped_column(String(20), default="stable")  # escalating | de_escalating | stable
    current_indicators: Mapped[dict] = mapped_column(JSONB, default=dict)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    event_cluster_count: Mapped[int] = mapped_column(Integer, default=0)
    algorithm_version: Mapped[str] = mapped_column(String(20))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class CrisisParticipant(Base):
    __tablename__ = "crisis_participants"
    __table_args__ = (UniqueConstraint("crisis_id", "participant_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    crisis_id: Mapped[int] = mapped_column(ForeignKey("crises.id", ondelete="CASCADE"), index=True)
    participant_key: Mapped[str] = mapped_column(String(140))  # "country:UKR" | "actor:RUS:MIL"
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"))
    role: Mapped[str] = mapped_column(String(40))  # core | involved | location | peripheral
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CrisisEvent(Base):
    """Membership of a deduplicated event cluster in a crisis, with the reason."""

    __tablename__ = "crisis_events"
    crisis_id: Mapped[int] = mapped_column(ForeignKey("crises.id", ondelete="CASCADE"), primary_key=True)
    event_cluster_id: Mapped[int] = mapped_column(ForeignKey("event_clusters.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    match_score: Mapped[float] = mapped_column(Float)
    reason: Mapped[dict] = mapped_column(JSONB, default=dict)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class CrisisArticle(Base):
    __tablename__ = "crisis_articles"
    crisis_id: Mapped[int] = mapped_column(ForeignKey("crises.id", ondelete="CASCADE"), primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)
    match_reason: Mapped[str | None] = mapped_column(String(200))


class CrisisEvidenceLog(Base):
    """Why the system created / updated / flagged a crisis."""

    __tablename__ = "crisis_evidence_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    crisis_id: Mapped[int] = mapped_column(ForeignKey("crises.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40))  # created | events_attached | participant_added | status_changed | review_flagged | reviewed
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    algorithm_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class CrisisSnapshot(Base):
    """Historical record of a crisis' indicators (never overwritten)."""

    __tablename__ = "crisis_snapshots"
    __table_args__ = (Index("ix_crisis_snapshots_crisis_asof", "crisis_id", "as_of"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    crisis_id: Mapped[int] = mapped_column(ForeignKey("crises.id", ondelete="CASCADE"))
    as_of: Mapped[datetime]
    priority: Mapped[float] = mapped_column(Float)
    priority_level: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(20))
    indicators: Mapped[dict] = mapped_column(JSONB, default=dict)
    participants: Mapped[list] = mapped_column(JSONB, default=list)
