import zipfile
from pathlib import Path

import pytest

from app.geopolitics import cameo
from app.geopolitics.models import ROLE_ACTOR1, ROLE_GOVERNMENT, ROLE_NON_STATE_CROSS_BORDER, ROLE_OCCURRED_IN
from app.geopolitics.normalize import GDELT_EVENT_COLUMNS, domain_of, normalize_gdelt, parse_row
from app.ingestion.connectors.gdelt_events import floor_slot, parse_lastupdate, read_export_zip, slot_key
from app.registry.service import RegistryResolver, normalize_name

FIX = Path(__file__).parents[1] / "fixtures"


def make_row(**kw) -> dict:
    row = dict.fromkeys(GDELT_EVENT_COLUMNS, "")
    row.update({"GLOBALEVENTID": "1", "SQLDATE": "20261119", "DATEADDED": "20261119093000", "EventCode": "190",
                "EventBaseCode": "190", "EventRootCode": "19", "QuadClass": "4", "GoldsteinScale": "-10",
                "NumMentions": "3", "NumSources": "1", "NumArticles": "3", "SOURCEURL": "https://www.example.com/a"})
    row.update(kw)
    return row


@pytest.fixture(scope="module")
def resolver(engine):
    from app.core.db import SessionLocal

    with SessionLocal() as s:
        return RegistryResolver.load(s)


def test_fixture_files_parse():
    rows = read_export_zip((FIX / "20261119093000.export.CSV.zip").read_bytes())
    assert len(rows) == 300
    assert all(len(r) == 61 for r in rows)
    with zipfile.ZipFile(FIX / "20261119093000.export.CSV.zip") as z:
        assert z.namelist() == ["20261119093000.export.CSV"]


def test_parse_row_rejects_wrong_width():
    assert parse_row(["x"] * 60) is None
    assert parse_row(["x"] * 61)["SOURCEURL"] == "x"


def test_lastupdate_parsing_uses_https_and_export_file():
    t, url, md5 = parse_lastupdate((FIX / "lastupdate.txt").read_text())
    assert slot_key(t) == "20261119094500"
    assert url.startswith("https://") and url.endswith(".export.CSV.zip")
    assert len(md5) == 32


def test_floor_slot():
    from datetime import datetime, timezone

    assert slot_key(floor_slot(datetime(2026, 11, 19, 9, 44, 59, tzinfo=timezone.utc))) == "20261119093000"


def test_fips_collision_is_mapped_explicitly(resolver):
    # FIPS "AU" = Austria, while ISO alpha-2 "AU" = Australia
    assert resolver.iso3(resolver.country_from_fips("AU")) == "AUT"
    assert resolver.iso3(resolver.country_from_fips("AS")) == "AUS"
    assert resolver.iso3(resolver.country_from_fips("UP")) == "UKR"
    assert resolver.iso3(resolver.country_from_fips("RS")) == "RUS"


def test_cameo_codes_and_overrides(resolver):
    assert resolver.iso3(resolver.country_from_cameo("UKR")) == "UKR"
    assert resolver.iso3(resolver.country_from_cameo("ROM")) == "ROU"
    assert resolver.country_from_cameo("AFR") is None  # regional code, not a country


def test_location_is_not_involvement(resolver):
    # A Russian military actor acting in Ukraine: Ukraine is the location only.
    ev = normalize_gdelt(make_row(Actor1Code="RUSMIL", Actor1Name="RUSSIA", Actor1CountryCode="RUS", Actor1Type1Code="MIL",
                                  ActionGeo_CountryCode="UP", ActionGeo_FullName="Kharkiv, Kharkivs'ka Oblast', Ukraine",
                                  ActionGeo_Lat="49.98", ActionGeo_Long="36.25", ActionGeo_Type="4"), resolver)
    roles = {(resolver.iso3(c), r) for c, r in ev.roles}
    assert ("UKR", ROLE_OCCURRED_IN) in roles
    assert ("RUS", ROLE_ACTOR1) in roles
    assert ("RUS", ROLE_GOVERNMENT) in roles
    assert ("UKR", ROLE_GOVERNMENT) not in roles
    assert ev.values["category"] == "fight" and ev.values["severity"] >= 0.85
    assert ev.cluster_key == "ev:2026-11-19:19:UKR:RUS"
    assert ev.country_iso3s == ["RUS", "UKR"]


def test_non_state_cross_border(resolver):
    ev = normalize_gdelt(make_row(Actor1Code="SDNREB", Actor1Name="SUDAN", Actor1CountryCode="SDN", Actor1Type1Code="REB",
                                  ActionGeo_CountryCode="CD", ActionGeo_FullName="Chad"), resolver)
    roles = {(resolver.iso3(c), r) for c, r in ev.roles}
    assert ("SDN", ROLE_NON_STATE_CROSS_BORDER) in roles
    assert ("SDN", ROLE_GOVERNMENT) not in roles


def test_invalid_dates_are_skipped(resolver):
    assert normalize_gdelt(make_row(SQLDATE="2026XX19"), resolver) is None


def test_cameo_severity_and_signals():
    assert cameo.severity("0871", "08") == 0.0
    assert cameo.signal_type("0871", "08", 2) == "de_escalation"
    assert cameo.signal_type("195", "19", 4) == "escalation"
    assert cameo.signal_type("130", "13", 3) == "diplomatic_tension"
    assert cameo.severity("195", "19") > cameo.severity("130", "13") > cameo.severity("010", "01")


def test_domain_and_name_helpers():
    assert domain_of("https://www.bbc.co.uk/news/x") == "bbc.co.uk"
    assert normalize_name("Côte d'Ivoire") == "cote d ivoire"
