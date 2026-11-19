"""Normalization of GDELT 2.0 event records.

* country codes: CAMEO actor codes and FIPS 10-4 geo codes -> ISO3 registry ids
* explicit country *roles* per event (location vs. actor affiliation vs.
  government involvement vs. cross-border non-state activity)
* CAMEO taxonomy -> category / severity / signal type
* deduplication key grouping reports of the same underlying event
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from app.geopolitics import cameo
from app.geopolitics.models import (
    ROLE_ACTOR1,
    ROLE_ACTOR2,
    ROLE_GOVERNMENT,
    ROLE_NON_STATE_CROSS_BORDER,
    ROLE_OCCURRED_IN,
)
from app.registry.service import ActorRef, RegistryResolver

NORMALIZATION_VERSION = "gdelt-v1"

GDELT_EVENT_COLUMNS = [
    "GLOBALEVENTID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode", "Actor1EthnicCode",
    "Actor1Religion1Code", "Actor1Religion2Code", "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code",
    "Actor2Code", "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode", "Actor2EthnicCode",
    "Actor2Religion1Code", "Actor2Religion2Code", "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code",
    "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode", "QuadClass", "GoldsteinScale",
    "NumMentions", "NumSources", "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode", "Actor1Geo_ADM1Code", "Actor1Geo_ADM2Code",
    "Actor1Geo_Lat", "Actor1Geo_Long", "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode", "Actor2Geo_ADM1Code", "Actor2Geo_ADM2Code",
    "Actor2Geo_Lat", "Actor2Geo_Long", "Actor2Geo_FeatureID",
    "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode", "ActionGeo_ADM1Code", "ActionGeo_ADM2Code",
    "ActionGeo_Lat", "ActionGeo_Long", "ActionGeo_FeatureID",
    "DATEADDED", "SOURCEURL",
]
assert len(GDELT_EVENT_COLUMNS) == 61


def parse_row(fields: list[str]) -> dict[str, str] | None:
    if len(fields) != 61:
        return None
    return dict(zip(GDELT_EVENT_COLUMNS, fields, strict=True))


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def parse_gdelt_ts(v: str) -> datetime:
    return datetime.strptime(v, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    return host[4:] if host.startswith("www.") else host or None


@dataclass
class NormalizedEvent:
    values: dict
    roles: list[tuple[int, str]]
    actor1: ActorRef | None
    actor2: ActorRef | None
    cluster_key: str | None
    cluster_title: str | None
    source_url: str | None
    country_iso3s: list[str] = field(default_factory=list)


def normalize_gdelt(raw: dict[str, str], resolver: RegistryResolver) -> NormalizedEvent | None:
    try:
        event_date = datetime.strptime(raw["SQLDATE"], "%Y%m%d").date()
        available_at = parse_gdelt_ts(raw["DATEADDED"])
    except (KeyError, ValueError):
        return None

    root = (raw.get("EventRootCode") or "").zfill(2) if raw.get("EventRootCode") else None
    code = raw.get("EventCode") or None
    quad = _int(raw.get("QuadClass"))

    a1_country = resolver.country_from_cameo(raw.get("Actor1CountryCode"))
    a2_country = resolver.country_from_cameo(raw.get("Actor2CountryCode"))
    action_country = resolver.country_from_fips(raw.get("ActionGeo_CountryCode"))
    if action_country is None and raw.get("ActionGeo_FullName"):
        action_country = resolver.country_from_name(raw["ActionGeo_FullName"].split(",")[-1])

    actor1 = resolver.actor_ref(
        raw.get("Actor1Code", ""), raw.get("Actor1Name", ""), raw.get("Actor1CountryCode", ""),
        raw.get("Actor1KnownGroupCode", ""), raw.get("Actor1Type1Code", ""),
    )
    actor2 = resolver.actor_ref(
        raw.get("Actor2Code", ""), raw.get("Actor2Name", ""), raw.get("Actor2CountryCode", ""),
        raw.get("Actor2KnownGroupCode", ""), raw.get("Actor2Type1Code", ""),
    )

    roles: set[tuple[int, str]] = set()
    if action_country:
        roles.add((action_country, ROLE_OCCURRED_IN))
    for cid, role, t1 in (
        (a1_country, ROLE_ACTOR1, raw.get("Actor1Type1Code", "")),
        (a2_country, ROLE_ACTOR2, raw.get("Actor2Type1Code", "")),
    ):
        if not cid:
            continue
        roles.add((cid, role))
        if t1 in cameo.GOVERNMENT_TYPES:
            roles.add((cid, ROLE_GOVERNMENT))
        if t1 in cameo.NON_STATE_ARMED_TYPES and action_country and action_country != cid:
            roles.add((cid, ROLE_NON_STATE_CROSS_BORDER))

    iso_action = resolver.iso3(action_country)
    actor_isos = sorted({x for x in (resolver.iso3(a1_country), resolver.iso3(a2_country)) if x})
    country_iso3s = sorted(set(actor_isos) | ({iso_action} if iso_action else set()))

    source_url = (raw.get("SOURCEURL") or "").strip() or None
    sev = cameo.severity(code, root)
    signal = cameo.signal_type(code, root, quad)

    cluster_key = None
    cluster_title = None
    if country_iso3s and root:
        cluster_key = f"ev:{event_date.isoformat()}:{root}:{iso_action or '-'}:{'-'.join(actor_isos) or '-'}"
        a1n = (raw.get("Actor1Name") or "").title() or "Unidentified actor"
        a2n = (raw.get("Actor2Name") or "").title()
        loc = raw.get("ActionGeo_FullName") or iso_action or ""
        cluster_title = f"{cameo.category_label(root)}: {a1n}{' → ' + a2n if a2n else ''}" + (f" ({loc})" if loc else "")

    values = dict(
        source_record_id=raw["GLOBALEVENTID"],
        normalization_version=NORMALIZATION_VERSION,
        event_date=event_date,
        available_at=available_at,
        event_code=code,
        event_base_code=raw.get("EventBaseCode") or None,
        event_root_code=root,
        category=cameo.category(root),
        quad_class=quad,
        goldstein=_float(raw.get("GoldsteinScale")),
        severity=sev,
        signal_type=signal,
        is_root_event=raw.get("IsRootEvent") == "1",
        num_mentions=_int(raw.get("NumMentions")) or 0,
        num_sources=_int(raw.get("NumSources")) or 0,
        num_articles=_int(raw.get("NumArticles")) or 0,
        avg_tone=_float(raw.get("AvgTone")),
        actor1_name=(raw.get("Actor1Name") or None),
        actor2_name=(raw.get("Actor2Name") or None),
        actor1_country_id=a1_country,
        actor2_country_id=a2_country,
        action_country_id=action_country,
        action_geo_type=_int(raw.get("ActionGeo_Type")),
        action_location=(raw.get("ActionGeo_FullName") or None),
        action_adm1=(raw.get("ActionGeo_ADM1Code") or None),
        latitude=_float(raw.get("ActionGeo_Lat")),
        longitude=_float(raw.get("ActionGeo_Long")),
        source_url=source_url,
        source_domain=domain_of(source_url),
        country_iso3s=country_iso3s,
    )
    return NormalizedEvent(values, sorted(roles), actor1, actor2, cluster_key, cluster_title, source_url, country_iso3s)


def event_date_from_key(key: str) -> date | None:
    try:
        return date.fromisoformat(key.split(":")[1])
    except (IndexError, ValueError):
        return None
