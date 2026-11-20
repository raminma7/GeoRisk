"""The simple 'Today' overview: one 'Crisis in <country>' card per country, with plain reasons."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.db import utcnow
from app.crises.models import Crisis
from app.geopolitics.models import EventCluster
from app.main import app
from app.monitoring import plain
from tests.conftest import FIXTURE_NOW


def add(db, uid, core, loc, level, mat, src, conf="medium"):
    db.add(Crisis(uid=uid, title="raw title", status="active", core_country_iso3s=core, location_country_iso3s=loc,
                  first_observed_at=FIXTURE_NOW, last_event_at=FIXTURE_NOW, algorithm_version="t", priority_level=level,
                  current_priority={"high": 60, "elevated": 40}.get(level, 10),
                  current_indicators={"material_conflict_24h": mat, "diplomatic_signals_24h": 4,
                                      "independent_sources_24h": src, "evidence_confidence_label": conf}, meta={}))


def violent(db, iso3, n):
    for i in range(n):
        db.add(EventCluster(cluster_key=f"k-{iso3}-{i}", kind="event", event_root_code="19", quad_class=4,
                            action_country_iso3=iso3, country_iso3s=[iso3], event_date=utcnow().date(),
                            first_seen_at=utcnow() - timedelta(hours=2), last_seen_at=utcnow() - timedelta(hours=1),
                            member_count=3, source_domains=["a.com", "b.com", "c.com"], max_severity=0.85))


def test_today_lists_countries(db, monkeypatch):
    monkeypatch.setattr(plain, "conflict_deaths_12m", lambda s: {"UKR": 30_000, "ISR": 2_000})
    add(db, "CR-1", ["RUS", "UKR"], ["UKR"], "elevated", 12, 80)
    add(db, "CR-2", ["ISR", "PSE"], ["ISR"], "high", 5, 60)
    add(db, "CR-3", ["IRN", "ISR", "USA"], ["IRN", "ISR"], "high", 5, 60)   # Israel is in two situations
    add(db, "CR-4", ["GBR"], ["GBR"], "high", 30, 90)                        # local reports only -> other signals
    violent(db, "UKR", 4)
    db.commit()
    c = TestClient(app)
    r = c.get("/api/v1/today").json()
    heads = [x["headline"] for x in r["countries"]]
    assert heads == ["Crisis in Ukraine", "Crisis in Israel", "Crisis in Iran"]
    ukr, isr = r["countries"][0], r["countries"][1]
    assert ukr["severity"]["label"] == "Critical"  # 30k verified deaths
    assert ukr["reasons"][0].startswith("About 30,000 people killed")
    assert "4 reports of attacks" in ukr["reasons"][1]
    assert [a["name"] for a in ukr["also_involved"]] == ["Russia"]  # Russia only takes part from outside here
    assert len(isr["situations"]) == 2 and isr["severity"]["label"] == "High"
    assert {a["iso3"] for a in isr["also_involved"]} == {"IRN", "PSE", "USA"}
    assert "Crisis in Russia" not in heads and "Crisis in United States" not in heads
    assert [x["headline"] for x in r["other_signals"]] == ["Crisis in United Kingdom"]
    assert r["summary"]["countries"] == 3 and r["summary"]["getting_worse"] == 0

    d = c.get("/api/v1/countries/UKR").json()
    assert d["story"]["headline"] == "Crisis in Ukraine" and len(d["story"]["activity"]) == 7
    assert d["story"]["activity"][-1]["violent"] == 4
    assert d["recent_incidents"][0]["text"].startswith("Fighting")


def country(iso3, level, yest, trend="flat", group="main"):
    return {"iso3": iso3, "headline": f"Crisis in {iso3}", "group": group, "severity": {"level": level},
            "severity_yesterday": yest, "trend": {"code": trend}}


def test_what_changed_only_with_a_full_day_to_compare(db):
    from sqlalchemy import select

    from app.ingestion.models import DataSource, IngestionBatch

    cs = [country("UKR", "critical", "high"), country("ISR", "high", "high", trend="down"), country("SDN", "high", None),
          country("GBR", "critical", "low", group="other")]
    w = plain.what_changed(db, cs, now=FIXTURE_NOW)
    assert w["items"] == [] and "full day" in w["note"]  # nothing collected yet: no fake 'worse than yesterday'

    src = db.scalar(select(DataSource.id).where(DataSource.key == "gdelt_events"))
    for i in range(48 * 4):
        db.add(IngestionBatch(data_source_id=src, batch_key=f"t{i}", batch_time=FIXTURE_NOW - timedelta(minutes=15 * i),
                              status="done"))
    db.commit()
    w = plain.what_changed(db, cs, now=FIXTURE_NOW)
    assert [i["text"] for i in w["items"]] == ["Crisis in UKR: worse than yesterday (High → Critical)",
                                               "New: Crisis in SDN", "Crisis in ISR: calming down"]
    assert w["note"] is None and w["items"][0]["iso3"] == "UKR"


def test_recent_news_signal_compares_with_normal_and_world(db):
    from datetime import date

    from app.geopolitics.models import NewsCountryMonthPart

    today = date(2026, 11, 20)

    def day(n, iso, events, violent):
        d = today - timedelta(days=n)
        db.add(NewsCountryMonthPart(file_key=f"{d:%Y%m%d}.export.CSV.zip", iso3=iso, month=d.replace(day=1),
                                    events=events, violent=violent))

    for n in range(1, 31):          # last 30 days
        day(n, "WLD", 100_000, 8_000)   # world: unchanged at 8%
        day(n, "ETH", 400, 56)          # Ethiopia: 14% violent ...
        day(n, "KHM", 400, 8)           # Cambodia: 2% violent ...
    for n in range(31, 396, 3):     # the 12 months before (every third day is enough)
        day(n, "WLD", 100_000, 8_000)
        day(n, "ETH", 400, 28)          # ... normally 7%
        day(n, "KHM", 400, 32)          # ... normally 8%
    db.commit()
    plain._signal_cache.update(at=0.0)
    sig = plain.recent_news_signal(db, today)
    assert sig["ETH"]["state"] == "surge" and sig["ETH"]["ratio"] == 2.0
    assert sig["KHM"]["state"] == "cooling"
    assert "WLD" not in sig
