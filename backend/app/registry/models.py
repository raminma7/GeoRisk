"""Shared geographical and actor registry.

Used by the geopolitical module today and by the economic module later.
ISO 3166 alpha-3 is the internal country identifier; every source-specific code
(CAMEO, FIPS 10-4, Gleditsch-Ward, ...) is an explicit row in
``source_code_mappings``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class Region(Base):
    __tablename__ = "regions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)  # e.g. "continent:asia", "un:western-asia"
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # continent | un_subregion | cameo_region | custom
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Country(Base):
    __tablename__ = "countries"
    id: Mapped[int] = mapped_column(primary_key=True)
    iso3: Mapped[str] = mapped_column(String(3), unique=True)
    iso2: Mapped[str | None] = mapped_column(String(2), index=True)
    iso_numeric: Mapped[int | None] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    official_name: Mapped[str | None] = mapped_column(String(300))
    continent_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"))
    subregion_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    is_historical: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    continent: Mapped[Region | None] = relationship(foreign_keys=[continent_id])
    subregion: Mapped[Region | None] = relationship(foreign_keys=[subregion_id])


class CountryAlias(Base):
    __tablename__ = "country_aliases"
    __table_args__ = (UniqueConstraint("country_id", "alias_norm"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(300))
    alias_norm: Mapped[str] = mapped_column(String(300), index=True)
    lang: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str | None] = mapped_column(String(60))


class CountryGroup(Base):
    __tablename__ = "country_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)  # "G7", "EU27", "NATO"
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # alliance | economic | political | custom
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class CountryGroupMember(Base):
    __tablename__ = "country_group_members"
    group_id: Mapped[int] = mapped_column(ForeignKey("country_groups.id", ondelete="CASCADE"), primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("countries.id", ondelete="CASCADE"), primary_key=True)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40))  # igo | alliance | economic | ngo | non_state_armed | other
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class Actor(Base):
    """A political actor. Geography is *not* involvement: an actor's country
    is its affiliation, and events link countries through explicit roles."""

    __tablename__ = "actors"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_key: Mapped[str] = mapped_column(String(120), unique=True)  # e.g. "RUS:GOV", "org:NAT", "UKR:REB"
    name: Mapped[str] = mapped_column(String(300))
    actor_type: Mapped[str] = mapped_column(String(40), index=True)
    # government | military | police | judiciary | legislature | intelligence | opposition | rebel |
    # insurgent | separatist | armed_group | civilian | media | business | igo | ngo | religious | unknown
    is_state_actor: Mapped[bool] = mapped_column(Boolean, default=False)
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"), index=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    country: Mapped[Country | None] = relationship()


class SourceCodeMapping(Base):
    """Explicit mapping from a source-specific code to a registry entity."""

    __tablename__ = "source_code_mappings"
    __table_args__ = (
        UniqueConstraint("scheme", "code"),
        Index("ix_source_code_mappings_entity", "entity_type", "entity_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scheme: Mapped[str] = mapped_column(String(40))  # cameo_country | fips | gw | cameo_region | cameo_igo | ucdp_*
    code: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(20))  # country | region | organization
    entity_id: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
