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


def fake_index(levels: dict[str, tuple[str, float, dict]]):
    def live(session, today=None, weights=None):
        return {iso: {"score": sc, "level": lv, "deaths_12m": 0, "news_events_30d": 5000,
                      "dimensions": {"violence": 50, "military": 40, "tension": 40, "unrest": 20, "momentum": 50, **dims}}
                for iso, (lv, sc, dims) in levels.items()}
    return live


def test_today_lists_countries_by_crisis_index(db, monkeypatch):
    from app.ml import crisis_index as CI

    monkeypatch.setattr(plain, "conflict_deaths_12m", lambda s: {"UKR": 30_000, "ISR": 2_000})
    monkeypatch.setattr(CI, "live", fake_index({
        "UKR": ("critical", 95, {"violence": 98}),
        "ISR": ("high", 88, {"tension": 99}),
        "IRN": ("elevated", 75, {"tension": 97}),
        "TWN": ("elevated", 73, {"military": 92, "violence": 2}),   # no situation, but a tense stand-off
        "GBR": ("low", 50, {}),
    }))
    add(db, "CR-1", ["RUS", "UKR"], ["UKR"], "elevated", 12, 80)
    add(db, "CR-2", ["ISR", "PSE"], ["ISR"], "high", 5, 60)
    add(db, "CR-3", ["IRN", "ISR", "USA"], ["IRN", "ISR"], "high", 5, 60)   # Israel is in two situations
    add(db, "CR-4", ["GBR"], ["GBR"], "high", 30, 90)                        # index Low -> other signals
    violent(db, "UKR", 4)
    db.commit()
    c = TestClient(app)
    r = c.get("/api/v1/today").json()
    heads = [x["headline"] for x in r["countries"]]
    assert heads == ["Crisis in Ukraine", "Crisis in Israel", "Crisis in Iran", "Crisis in Taiwan"]
    ukr, isr, irn, twn = r["countries"]
    assert ukr["severity"]["label"] == "Critical" and ukr["index"]["dimensions"]["violence"] == 98
    assert ukr["reasons"][0].startswith("About 30,000 people killed")
    assert "4 reports of attacks" in " ".join(ukr["reasons"])
    assert [a["name"] for a in ukr["also_involved"]] == ["Russia"]  # Russia only takes part from outside here
    assert len(isr["situations"]) == 2 and isr["severity"]["label"] == "High"
    assert irn["severity"]["label"] == "Moderate" and irn["reasons"][0].startswith("Very tense")  # not deaths alone
    assert twn["kind_label"] == "Military tension" and twn["situations"] == []
    assert [x["headline"] for x in r["other_signals"]] == ["Crisis in United Kingdom"]
    assert r["summary"]["countries"] == 4 and r["summary"]["by_level"]["elevated"] == 2

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


def test_crisis_index_live_from_news_and_deaths(db, monkeypatch):
    """Live index from GDELT daily files: tension-heavy news raises a country without deaths; a country with little
    news is pulled towards the world average; verified deaths set the violence dimension."""
    from datetime import date

    from app.geopolitics.models import NewsCountryMonthPart
    from app.ml import crisis_index as CI

    today = date(2026, 11, 20)
    monkeypatch.setattr(plain, "conflict_deaths_12m", lambda s: {"AAA": 20_000})

    def part(d, iso, ev, v, mc, th, dt, pr, month=None):
        db.add(NewsCountryMonthPart(file_key=f"{d:%Y%m%d}.export.CSV.zip", iso3=iso, month=month or d.replace(day=1),
                                    events=ev, violent=v, material_conflict=mc, threats=th, diplomatic_tension=dt,
                                    protests=pr))

    # history 1990-2025 (reference distribution): ordinary months
    for y in range(1990, 2026):
        for k, iso in enumerate(("AAA", "BBB", "CCC", "DDD", "EEE", "WLD")):
            t = 20 + ((y * 7 + k * 13) % 30) * 30       # tension share spread from 2% to 89%
            part(date(y, 6, 1), iso, 1000, 50, 110, t // 2, t // 2, 5, month=date(y, 6, 1))
    # last year and last 30 days
    for n in range(1, 360, 7):
        d = today - timedelta(days=n)
        part(d, "WLD", 100_000, 8_000, 14_000, 5_000, 8_000, 900)
        part(d, "AAA", 3000, 300, 450, 150, 250, 30)      # at war
        part(d, "BBB", 3000, 60, 150, 600 if n <= 30 else 150, 700 if n <= 30 else 200, 20)  # tension rising
        part(d, "CCC", 80, 4, 20, 30, 30, 0)              # little news (~400 events a month): unreliable shares
    db.commit()
    CI._cache.clear()
    monkeypatch.setattr(CI, "cuts_for", lambda s, w=None: {"critical": 85.0, "high": 75.0, "elevated": 60.0})
    ix = CI.live(db, today, weights=CI.DEFAULT_WEIGHTS)
    a, b, c = ix["AAA"], ix["BBB"], ix["CCC"]
    assert a["dimensions"]["violence"] == 100 and a["level"] == "critical"
    assert b["dimensions"]["tension"] > 95 and b["dimensions"]["momentum"] > 60 and b["level"] != "low"
    assert c["dimensions"]["tension"] < b["dimensions"]["tension"]   # shrunk towards the world average


def test_outlook_note_uses_the_same_trend_as_the_card(db, monkeypatch):
    """Iran case: the card says 'Getting worse' because the level rose since yesterday (direction 'stable'); the
    outlook keeps the model's 'Could get better' but explains it with what history says."""
    from datetime import date

    from app.crises.models import CrisisSnapshot
    from app.ml import predictions as P
    from app.ml.models import MlPrediction

    add(db, "CR-9", ["IRN", "USA"], ["IRN"], "critical", 40, 300, conf="high")
    db.flush()
    cid = db.query(Crisis).filter_by(uid="CR-9").one().id
    db.add(CrisisSnapshot(crisis_id=cid, as_of=utcnow() - timedelta(hours=25), priority=60, priority_level="high",
                          direction="stable", indicators={}, participants=[]))
    for model, prob, status in ((P.MODELS[3], 0.01, "predicted"), (P.CALM_MODELS[3], 0.87, "predicted")):
        db.add(MlPrediction(model_name=model, model_version="vT", iso3="IRN", window_start=date(2026, 10, 1),
                            window_end=date(2026, 12, 1), data_through=date(2026, 9, 1), status=status,
                            current_tier="critical", probability=prob, raw_score=prob, rank=1, out_of=1,
                            times_average=1.0, level="low", reasons=[]))
    db.commit()
    monkeypatch.setattr(plain, "conflict_deaths_12m", lambda s: {"IRN": 24_000})
    assert plain.country_trends(db)["IRN"] == "up"
    o = P.outlooks(db, 3)["IRN"]
    assert o["direction"] == "better" and "faded within 3 months" in o["note"]
