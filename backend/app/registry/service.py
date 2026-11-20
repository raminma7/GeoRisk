"""Registry seeding and fast in-memory resolution of source codes.

``RegistryResolver`` caches mappings so high-volume ingestion does not hit the
database per field.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from importlib import resources

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.geopolitics.cameo import ACTOR_TYPE_MAP, GOVERNMENT_TYPES, NON_STATE_ARMED_TYPES
from app.registry.models import (
    Actor,
    Country,
    CountryAlias,
    CountryGroup,
    CountryGroupMember,
    Organization,
    Region,
    SourceCodeMapping,
)


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_name(s)).strip("-")


def load_seed() -> dict:
    with resources.files("app.registry").joinpath("data/countries.json").open(encoding="utf-8") as f:
        return json.load(f)


def seed_registry(session: Session) -> dict[str, int]:
    """Idempotently load countries, regions, aliases, groups, organizations and code mappings."""
    data = load_seed()
    stats = {"regions": 0, "countries": 0, "mappings": 0}

    def upsert_region(code: str, name: str, kind: str, parent_id: int | None = None) -> int:
        existing = session.scalar(select(Region).where(Region.code == code))
        if existing:
            return existing.id
        r = Region(code=code, name=name, kind=kind, parent_id=parent_id, meta={})
        session.add(r)
        session.flush()
        stats["regions"] += 1
        return r.id

    UCDP_GW_EXTRA = {"678": "YEM", "345": "SRB"}

    def add_mapping(scheme: str, code: str, entity_type: str, entity_id: int, notes: str | None = None) -> None:
        stmt = insert(SourceCodeMapping).values(
            scheme=scheme, code=code, entity_type=entity_type, entity_id=entity_id, notes=notes
        ).on_conflict_do_nothing(index_elements=["scheme", "code"]).returning(SourceCodeMapping.id)
        if session.execute(stmt).first():
            stats["mappings"] += 1

    for c in data["countries"]:
        cont_id = upsert_region(f"continent:{slug(c['continent'])}", c["continent"], "continent") if c["continent"] else None
        sub_id = (
            upsert_region(f"un:{slug(c['subregion'])}", c["subregion"], "un_subregion", cont_id)
            if c["subregion"]
            else None
        )
        country = session.scalar(select(Country).where(Country.iso3 == c["iso3"]))
        if not country:
            country = Country(
                iso3=c["iso3"],
                iso2=c["iso2"],
                iso_numeric=c["iso_numeric"],
                name=c["name"],
                official_name=c["official_name"],
                continent_id=cont_id,
                subregion_id=sub_id,
                is_historical=c["is_historical"],
                meta={"gw_code": c["gw_code"], "alias_regex": c["alias_regex"]},
            )
            session.add(country)
            session.flush()
            stats["countries"] += 1
        for alias in {*c["aliases"], c["iso3"]}:
            session.execute(
                insert(CountryAlias)
                .values(country_id=country.id, alias=alias, alias_norm=normalize_name(alias), lang="en", source="seed")
                .on_conflict_do_nothing()
            )
        if not c["is_historical"]:
            add_mapping("cameo_country", c["iso3"], "country", country.id)
            for f in c["fips"]:
                add_mapping("fips", f, "country", country.id)
        if c["gw_code"]:
            add_mapping("gw", str(c["gw_code"]), "country", country.id)
        # UCDP uses its own Gleditsch–Ward codes for two countries: Yemen 678 (unified Yemen keeps North Yemen's code;
        # our registry's 680 is former South Yemen) and Serbia 345 (as successor of Yugoslavia).
        for gw, iso3 in UCDP_GW_EXTRA.items():
            if c["iso3"] == iso3:
                add_mapping("gw", gw, "country", country.id, notes="UCDP Gleditsch-Ward code")
        for g in c["groups"]:
            grp = session.scalar(select(CountryGroup).where(CountryGroup.code == g))
            if not grp:
                grp = CountryGroup(code=g, name=g, kind=data["groups"][g], meta={})
                session.add(grp)
                session.flush()
            session.execute(
                insert(CountryGroupMember).values(group_id=grp.id, country_id=country.id).on_conflict_do_nothing()
            )

    iso3_ids = dict(session.execute(select(Country.iso3, Country.id)).all())
    for cameo, iso3 in data["cameo_to_iso3"].items():
        if iso3 in iso3_ids:
            add_mapping("cameo_country", cameo, "country", iso3_ids[iso3], "CAMEO code differs from ISO3")
    for code, name in data["cameo_regions"].items():
        rid = upsert_region(f"cameo:{code.lower()}", name, "cameo_region")
        add_mapping("cameo_region", code, "region", rid)
    for code, org in data["cameo_organizations"].items():
        o = session.scalar(select(Organization).where(Organization.code == code))
        if not o:
            o = Organization(code=code, name=org["name"], kind=org["kind"], meta={"source": "cameo"})
            session.add(o)
            session.flush()
        add_mapping("cameo_igo", code, "organization", o.id)
    session.flush()
    return stats


@dataclass
class ActorRef:
    key: str
    name: str
    actor_type: str
    is_state: bool
    country_iso3: str | None
    org_code: str | None


@dataclass
class RegistryResolver:
    """In-memory lookup tables built from the database."""

    country_by_iso3: dict[str, int] = field(default_factory=dict)
    iso3_by_id: dict[int, str] = field(default_factory=dict)
    cameo_country: dict[str, int] = field(default_factory=dict)
    fips_country: dict[str, int] = field(default_factory=dict)
    gw_country: dict[str, int] = field(default_factory=dict)
    alias_country: dict[str, int] = field(default_factory=dict)
    org_by_code: dict[str, int] = field(default_factory=dict)
    region_by_country: dict[int, int | None] = field(default_factory=dict)
    actor_ids: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session) -> RegistryResolver:
        r = cls()
        for cid, iso3, sub in session.execute(select(Country.id, Country.iso3, Country.subregion_id)):
            r.country_by_iso3[iso3] = cid
            r.iso3_by_id[cid] = iso3
            r.region_by_country[cid] = sub
        for scheme, code, etype, eid in session.execute(
            select(SourceCodeMapping.scheme, SourceCodeMapping.code, SourceCodeMapping.entity_type, SourceCodeMapping.entity_id)
        ):
            if etype == "country":
                {"cameo_country": r.cameo_country, "fips": r.fips_country, "gw": r.gw_country}.get(scheme, {})[code] = eid
            elif etype == "organization" and scheme == "cameo_igo":
                r.org_by_code[code] = eid
        for alias_norm, cid in session.execute(select(CountryAlias.alias_norm, CountryAlias.country_id)):
            r.alias_country.setdefault(alias_norm, cid)
        for key, aid in session.execute(select(Actor.actor_key, Actor.id)):
            r.actor_ids[key] = aid
        return r

    def country_from_cameo(self, code: str | None) -> int | None:
        return self.cameo_country.get(code) if code else None

    def country_from_fips(self, code: str | None) -> int | None:
        return self.fips_country.get(code) if code else None

    def country_from_name(self, name: str | None) -> int | None:
        return self.alias_country.get(normalize_name(name)) if name else None

    def iso3(self, country_id: int | None) -> str | None:
        return self.iso3_by_id.get(country_id) if country_id else None

    def actor_ref(
        self, code: str, name: str, country_code: str, known_group: str, type1: str
    ) -> ActorRef | None:
        """Derive a stable, coarse actor identity from GDELT actor fields."""
        if not (code or name):
            return None
        iso3 = self.iso3(self.country_from_cameo(country_code))
        if known_group and known_group in self.org_by_code:
            return ActorRef(f"org:{known_group}", name or known_group, "igo", False, None, known_group)
        t = type1 or ""
        actor_type = ACTOR_TYPE_MAP.get(t, "unknown" if t else ("state" if iso3 and code == country_code else "unknown"))
        if not t and iso3 and code == country_code:
            actor_type = "country"  # actor coded only by nationality (e.g. "RUSSIA")
        is_state = t in GOVERNMENT_TYPES or actor_type == "country"
        if t in NON_STATE_ARMED_TYPES:
            is_state = False
        key = f"{iso3 or 'XXX'}:{t or ('COUNTRY' if actor_type == 'country' else (code or 'UNK')[:20])}"
        label = name.title() if name else key
        if actor_type == "country" and iso3:
            label = name.title() if name else iso3
        return ActorRef(key, label[:300], actor_type, is_state, iso3, None)

    def ensure_actor(self, session: Session, ref: ActorRef) -> int:
        aid = self.actor_ids.get(ref.key)
        if aid:
            return aid
        stmt = (
            insert(Actor)
            .values(
                actor_key=ref.key,
                name=ref.name,
                actor_type=ref.actor_type,
                is_state_actor=ref.is_state,
                country_id=self.country_by_iso3.get(ref.country_iso3) if ref.country_iso3 else None,
                organization_id=self.org_by_code.get(ref.org_code) if ref.org_code else None,
                first_seen_at=utcnow(),
                last_seen_at=utcnow(),
                meta={},
            )
            .on_conflict_do_update(index_elements=["actor_key"], set_={"last_seen_at": utcnow()})
            .returning(Actor.id)
        )
        aid = session.execute(stmt).scalar_one()
        self.actor_ids[ref.key] = aid
        return aid
