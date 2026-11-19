"""Geopolitical event, news and relationship storage.

Raw records are immutable. Normalized events reference their raw record and
carry a ``normalization_version``; re-normalizing writes new rows rather than
overwriting history.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow

# Verification statuses — deliberately conservative wording.
VERIFICATION_MACHINE_EXTRACTED = "machine_extracted"  # single automated extraction (GDELT default)
VERIFICATION_MULTI_SOURCE = "multi_source_reported"  # reported by >= N independent outlets; still unverified
VERIFICATION_DATASET = "curated_dataset"  # human-coded research dataset (UCDP)
VERIFICATION_STATUSES = [VERIFICATION_MACHINE_EXTRACTED, VERIFICATION_MULTI_SOURCE, VERIFICATION_DATASET]

# Country roles in an event (spec §5 — geography is not involvement)
ROLE_OCCURRED_IN = "occurred_in"
ROLE_ACTOR1 = "actor1_affiliation"
ROLE_ACTOR2 = "actor2_affiliation"
ROLE_GOVERNMENT = "government_involved"
ROLE_NON_STATE_CROSS_BORDER = "non_state_cross_border"
ROLE_MEDIA_ORIGIN = "media_origin"


class RawGeopoliticalEvent(Base):
    __tablename__ = "raw_geopolitical_events"
    __table_args__ = (UniqueConstraint("data_source_id", "source_record_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    source_record_id: Mapped[str] = mapped_column(String(80))
    batch_key: Mapped[str | None] = mapped_column(String(80), index=True)
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    available_at: Mapped[datetime | None]  # when the source published the record
    ingested_at: Mapped[datetime] = mapped_column(default=utcnow)
    raw: Mapped[dict] = mapped_column(JSONB)


class NormalizedGeopoliticalEvent(Base):
    __tablename__ = "normalized_geopolitical_events"
    __table_args__ = (
        UniqueConstraint("data_source_id", "source_record_id", "normalization_version"),
        Index("ix_nge_available_at", "available_at"),
        Index("ix_nge_event_date", "event_date"),
        Index("ix_nge_action_country", "action_country_id", "event_date"),
        Index("ix_nge_root_quad", "event_root_code", "quad_class"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(ForeignKey("raw_geopolitical_events.id"), index=True)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    source_record_id: Mapped[str] = mapped_column(String(80))
    normalization_version: Mapped[str] = mapped_column(String(20))
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("normalized_geopolitical_events.id"))

    event_date: Mapped[date] = mapped_column(Date)  # when the event reportedly happened
    available_at: Mapped[datetime]  # when the record became available (GDELT DATEADDED)
    ingested_at: Mapped[datetime] = mapped_column(default=utcnow)

    event_code: Mapped[str | None] = mapped_column(String(6))
    event_base_code: Mapped[str | None] = mapped_column(String(6))
    event_root_code: Mapped[str | None] = mapped_column(String(3))
    category: Mapped[str | None] = mapped_column(String(60))  # human-readable CAMEO root category
    quad_class: Mapped[int | None] = mapped_column(SmallInteger)
    goldstein: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1 heuristic, see geopolitics.cameo
    signal_type: Mapped[str | None] = mapped_column(String(30))  # escalation | de_escalation | diplomatic | neutral
    is_root_event: Mapped[bool] = mapped_column(Boolean, default=False)
    num_mentions: Mapped[int] = mapped_column(Integer, default=0)
    num_sources: Mapped[int] = mapped_column(Integer, default=0)
    num_articles: Mapped[int] = mapped_column(Integer, default=0)
    avg_tone: Mapped[float | None] = mapped_column(Float)

    actor1_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"))
    actor2_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"))
    actor1_name: Mapped[str | None] = mapped_column(String(300))
    actor2_name: Mapped[str | None] = mapped_column(String(300))
    actor1_country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    actor2_country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    action_country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    action_geo_type: Mapped[int | None] = mapped_column(SmallInteger)
    action_location: Mapped[str | None] = mapped_column(String(300))
    action_adm1: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    source_url: Mapped[str | None] = mapped_column(Text)
    source_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    verification_status: Mapped[str] = mapped_column(String(30), default=VERIFICATION_MACHINE_EXTRACTED)
    country_iso3s: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)  # involved+location, denormalized

    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("event_clusters.id"), index=True)


class EventCountryRole(Base):
    __tablename__ = "event_country_roles"
    __table_args__ = (Index("ix_ecr_country_role", "country_id", "role"),)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_geopolitical_events.id", ondelete="CASCADE"), primary_key=True
    )
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(40), primary_key=True)


class EventCluster(Base):
    """Deduplicated grouping of reports describing the same underlying event."""

    __tablename__ = "event_clusters"
    __table_args__ = (Index("ix_event_clusters_kind_last", "kind", "last_seen_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cluster_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # event | news
    title: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date | None] = mapped_column(Date)
    event_root_code: Mapped[str | None] = mapped_column(String(3))
    quad_class: Mapped[int | None] = mapped_column(SmallInteger)
    country_iso3s: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    action_country_iso3: Mapped[str | None] = mapped_column(String(3))
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    source_domains: Mapped[list[str]] = mapped_column(ARRAY(String(255)), default=list)
    max_severity: Mapped[float] = mapped_column(Float, default=0.0)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    verification_status: Mapped[str] = mapped_column(String(30), default=VERIFICATION_MACHINE_EXTRACTED)
    representative_event_id: Mapped[int | None] = mapped_column(BigInteger)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class NewsArticle(Base):
    """News article metadata discovered via GDELT DOC. No full text is stored."""

    __tablename__ = "news_articles"
    __table_args__ = (Index("ix_news_seen_at", "seen_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    language: Mapped[str | None] = mapped_column(String(40), index=True)
    source_country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"), index=True)
    source_country_name: Mapped[str | None] = mapped_column(String(120))
    seen_at: Mapped[datetime]
    ingested_at: Mapped[datetime] = mapped_column(default=utcnow)
    data_source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    query_keys: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list)
    topic: Mapped[str | None] = mapped_column(String(60))
    country_iso3s: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)  # countries the article is about
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("event_clusters.id"), index=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class DiplomaticRelation(Base):
    """Reference data on formal relations (embassies, severed ties, treaties)."""

    __tablename__ = "diplomatic_relations"
    id: Mapped[int] = mapped_column(primary_key=True)
    country_a_id: Mapped[int] = mapped_column(ForeignKey("countries.id"))
    country_b_id: Mapped[int] = mapped_column(ForeignKey("countries.id"))
    relation_type: Mapped[str] = mapped_column(String(60))  # diplomatic_ties | alliance | treaty | severed
    status: Mapped[str] = mapped_column(String(40))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class CountryRelationship(Base):
    """Directed, windowed interaction edge between two countries (graph model)."""

    __tablename__ = "country_relationships"
    __table_args__ = (
        UniqueConstraint("source_country_id", "target_country_id", "window_start", "window_days", "algorithm_version"),
        Index("ix_country_rel_window", "window_end"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"))
    target_country_id: Mapped[int] = mapped_column(ForeignKey("countries.id"))
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    window_days: Mapped[int] = mapped_column(Integer)
    event_clusters: Mapped[int] = mapped_column(Integer, default=0)
    material_conflict: Mapped[int] = mapped_column(Integer, default=0)
    verbal_conflict: Mapped[int] = mapped_column(Integer, default=0)
    verbal_cooperation: Mapped[int] = mapped_column(Integer, default=0)
    material_cooperation: Mapped[int] = mapped_column(Integer, default=0)
    avg_goldstein: Mapped[float | None] = mapped_column(Float)
    algorithm_version: Mapped[str] = mapped_column(String(20))
    computed_at: Mapped[datetime] = mapped_column(default=utcnow)


class GeopoliticalIndicator(Base):
    """Time series of observed tension indicators for any scope."""

    __tablename__ = "geopolitical_indicators"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_key", "indicator", "window", "as_of", "algorithm_version"),
        Index("ix_geo_ind_scope", "scope_type", "scope_key", "indicator", "as_of"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(20))  # country | country_pair | crisis | region | global | news_query
    scope_key: Mapped[str] = mapped_column(String(200))
    indicator: Mapped[str] = mapped_column(String(80))
    window: Mapped[str] = mapped_column(String(10))  # 1d | 7d | 30d | 15m
    as_of: Mapped[datetime]
    value: Mapped[float | None] = mapped_column(Float)
    baseline_mean: Mapped[float | None] = mapped_column(Float)
    baseline_std: Mapped[float | None] = mapped_column(Float)
    zscore: Mapped[float | None] = mapped_column(Float)
    algorithm_version: Mapped[str] = mapped_column(String(20))
    computed_at: Mapped[datetime] = mapped_column(default=utcnow)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class UcdpEvent(Base):
    """UCDP Georeferenced Event Dataset record (human-coded, versioned)."""

    __tablename__ = "ucdp_events"
    __table_args__ = (
        UniqueConstraint("dataset_version_id", "ucdp_id"),
        Index("ix_ucdp_country_date", "country_id", "date_start"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"))
    ucdp_id: Mapped[str] = mapped_column(String(40))
    conflict_id: Mapped[str | None] = mapped_column(String(40), index=True)
    conflict_name: Mapped[str | None] = mapped_column(String(300))
    dyad_id: Mapped[str | None] = mapped_column(String(40))
    dyad_name: Mapped[str | None] = mapped_column(String(300))
    type_of_violence: Mapped[int] = mapped_column(SmallInteger)  # 1 state-based, 2 non-state, 3 one-sided
    side_a: Mapped[str | None] = mapped_column(String(300))
    side_b: Mapped[str | None] = mapped_column(String(300))
    side_a_gw: Mapped[str | None] = mapped_column(String(60))
    side_b_gw: Mapped[str | None] = mapped_column(String(60))
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"))
    country_name: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    date_start: Mapped[date] = mapped_column(Date)
    date_end: Mapped[date] = mapped_column(Date)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    where_description: Mapped[str | None] = mapped_column(Text)
    deaths_best: Mapped[int] = mapped_column(Integer, default=0)
    deaths_low: Mapped[int] = mapped_column(Integer, default=0)
    deaths_high: Mapped[int] = mapped_column(Integer, default=0)
    event_clarity: Mapped[int | None] = mapped_column(SmallInteger)
    date_precision: Mapped[int | None] = mapped_column(SmallInteger)
    is_interstate: Mapped[bool] = mapped_column(Boolean, default=False)
    available_at: Mapped[datetime]  # release date of the dataset version
    raw: Mapped[dict] = mapped_column(JSONB)
