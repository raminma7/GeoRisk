"""Crisis discovery, multi-country crises, priority explainability, alerts with evidence.

The deterministic scenario below inserts *synthetic test clusters* (clearly
labelled, test-only) so assertions do not depend on what real news happened
to contain; a second test runs the pipeline on the real GDELT fixtures.
"""

from datetime import timedelta

from sqlalchemy import func, select

from app.crises.engine import CrisisEngine
from app.crises.models import Crisis, CrisisEvent, CrisisEvidenceLog, CrisisParticipant
from app.geopolitics.models import EventCluster
from app.monitoring.alerts import evaluate_alerts
from app.monitoring.models import GeopoliticalAlert, MonitoringPriority
from app.monitoring.priority import score_crisis
from tests.conftest import FIXTURE_NOW

N = 0


def add_cluster(db, countries, action, root="19", quad=4, sev=0.85, domains=("a.com", "b.com", "c.com"),
                hours_ago=2, members=4):
    global N
    N += 1
    t = FIXTURE_NOW - timedelta(hours=hours_ago)
    c = EventCluster(cluster_key=f"test:{N}", kind="event", title=f"[synthetic test] cluster {N}", event_date=t.date(),
                     event_root_code=root, quad_class=quad, country_iso3s=sorted(countries), action_country_iso3=action,
                     first_seen_at=t, last_seen_at=t, member_count=members, source_domains=list(domains),
                     max_severity=sev, latitude=48.0, longitude=37.0,
                     verification_status="multi_source_reported" if len(domains) >= 3 else "machine_extracted", meta={})
    db.add(c)
    db.flush()
    return c


def scenario(db):
    """Three-country theater: AAA... using real ISO codes UKR/RUS/BLR, plus noise elsewhere."""
    for i in range(8):
        add_cluster(db, {"UKR", "RUS"}, "UKR", domains=(f"s{i}.com", f"t{i}.com", "x.com"))
    for i in range(4):
        add_cluster(db, {"UKR", "BLR"}, "UKR", root="13", quad=3, sev=0.4, domains=(f"b{i}.org",))
    for i in range(3):
        add_cluster(db, {"BRA"}, "BRA", root="14", quad=3, sev=0.3, domains=("br.com",))
    db.commit()


def test_crisis_discovery_multi_country(db):
    scenario(db)
    stats = CrisisEngine(db, now=FIXTURE_NOW).run()
    db.commit()
    assert stats["created"] == 1
    crisis = db.scalar(select(Crisis))
    assert "UKR" in crisis.location_country_iso3s
    participants = set(db.scalars(select(CrisisParticipant.participant_key)))
    assert {"country:UKR", "country:RUS", "country:BLR"} <= participants  # more than two countries
    assert db.scalar(select(func.count()).select_from(CrisisEvent)) == 12  # Brazil noise not attached
    log = db.scalar(select(CrisisEvidenceLog).where(CrisisEvidenceLog.action == "created"))
    assert log.evidence["criteria"]["independent_sources"] >= 4 and log.evidence["seed_cluster_ids"]
    assert crisis.uid.startswith("CR-2026-")


def test_new_evidence_attaches_and_ambiguous_flags_review(db):
    scenario(db)
    CrisisEngine(db, now=FIXTURE_NOW).run()
    db.commit()
    attach = add_cluster(db, {"UKR"}, "UKR", hours_ago=1)
    # partial overlap, different location: 0.45*2/3 + 0.25*2/4 = 0.425 -> review band [0.40, 0.55)
    ambiguous = add_cluster(db, {"POL", "UKR", "RUS"}, "POL", hours_ago=1)
    unrelated = add_cluster(db, {"POL", "UKR"}, "POL", hours_ago=1)  # 0.29 -> left for discovery
    db.commit()
    CrisisEngine(db, now=FIXTURE_NOW).run()
    db.commit()
    rows = dict(db.execute(select(CrisisEvent.event_cluster_id, CrisisEvent.needs_review)).all())
    assert rows[attach.id] is False
    assert rows.get(ambiguous.id) is True
    assert unrelated.id not in rows
    crisis = db.scalar(select(Crisis))
    assert crisis.needs_review is True
    assert db.scalar(select(func.count()).where(CrisisEvidenceLog.action == "review_flagged")) == 1


def test_duplicate_theaters_merge(db):
    for i in range(8):
        add_cluster(db, {"UKR", "RUS"}, "UKR", domains=(f"s{i}.com", "x.com", "y.com"))
        add_cluster(db, {"UKR", "RUS"}, "RUS", domains=(f"r{i}.com", "x.com", "y.com"))
    db.commit()
    CrisisEngine(db, now=FIXTURE_NOW).run()
    db.commit()
    live = db.scalars(select(Crisis).where(Crisis.status != "merged")).all()
    assert len(live) == 1
    assert set(live[0].location_country_iso3s) == {"RUS", "UKR"}


def test_priority_is_explainable_and_alerts_carry_evidence(db):
    scenario(db)
    CrisisEngine(db, now=FIXTURE_NOW).run()
    db.commit()
    crisis = db.scalar(select(Crisis))
    mp = score_crisis(db, crisis, now=FIXTURE_NOW)
    db.commit()
    expected = {"severity", "change_vs_baseline", "corroboration", "diplomatic_signals", "evidence_completeness",
                "freshness", "new_development"}
    assert expected <= set(mp.components)
    assert abs(sum(c["contribution"] for k, c in mp.components.items() if k in expected) - mp.score) < 0.05
    assert mp.algorithm_version == "priority-v1" and mp.supporting_event_cluster_ids
    assert mp.independent_source_count >= 4
    assert "not a probability" in mp.explanation
    # baseline is insufficient with no history -> flagged, never invented
    assert mp.components["change_vs_baseline"]["inputs"]["method"] in ("unavailable", "short_term_damped")
    alerts = evaluate_alerts(db, [mp])
    db.commit()
    assert any(a.alert_type == "new_crisis" for a in alerts)
    for a in alerts:
        assert a.evidence["supporting_event_clusters"]
        assert a.evidence["independent_source_count"] == mp.independent_source_count
        assert a.evidence["algorithm_version"] == "priority-v1"
    # re-evaluating the same priority does not duplicate alerts
    evaluate_alerts(db, [mp])
    db.commit()
    assert db.scalar(select(func.count()).select_from(GeopoliticalAlert)) == len(alerts)


def test_media_volume_alone_does_not_drive_score(db):
    scenario(db)
    CrisisEngine(db, now=FIXTURE_NOW).run()
    db.commit()
    crisis = db.scalar(select(Crisis))
    base = score_crisis(db, crisis, now=FIXTURE_NOW).score
    # inflate report counts massively without new outlets or new incidents
    db.execute(EventCluster.__table__.update().values(member_count=EventCluster.member_count * 500))
    db.commit()
    inflated = score_crisis(db, crisis, now=FIXTURE_NOW).score
    assert inflated - base < 8


def ingested_history(db, hours=96):
    """Mark every 15-minute GDELT batch of the last `hours` as ingested (trends need data on both sides)."""
    from app.ingestion.models import DataSource, IngestionBatch

    src = db.scalar(select(DataSource.id).where(DataSource.key == "gdelt_events"))
    for i in range(hours * 4):
        t = FIXTURE_NOW - timedelta(minutes=15 * i)
        db.add(IngestionBatch(data_source_id=src, batch_key=f"t{i}", batch_time=t, status="done"))
    db.commit()


def test_de_escalation_direction(db):
    # strong conflict 2-4 days ago, quiet conflict + cooperation in the last day
    ingested_history(db)
    for i in range(10):
        add_cluster(db, {"UKR", "RUS"}, "UKR", hours_ago=50 + i, domains=(f"s{i}.com", "x.com", "y.com"))
    db.commit()
    CrisisEngine(db, now=FIXTURE_NOW - timedelta(hours=40)).run()
    db.commit()
    crisis = db.scalar(select(Crisis))
    for i in range(3):
        c = add_cluster(db, {"UKR", "RUS"}, "UKR", root="08", quad=2, sev=0.0, hours_ago=2, domains=(f"p{i}.com",))
        db.add(CrisisEvent(crisis_id=crisis.id, event_cluster_id=c.id, match_score=1.0, reason={}))
    db.commit()
    mp = score_crisis(db, crisis, now=FIXTURE_NOW)
    assert mp.direction == "de_escalating"
    assert mp.components["_direction"]["inputs"]["data_coverage_prior_72h"] == 1.0
    alerts = evaluate_alerts(db, [mp])
    assert any(a.alert_type == "de_escalation" for a in alerts)


def test_pipeline_on_real_fixtures(ingested, monkeypatch):
    from app.workers import pipeline

    out = pipeline.run_monitoring_pipeline(now=FIXTURE_NOW)
    assert out["indicators"]["countries"] > 20
    assert out["priorities"] == ingested.scalar(select(func.count()).select_from(Crisis).where(
        Crisis.status.in_(["candidate", "active", "dormant"])))
    assert ingested.scalar(select(func.count()).select_from(MonitoringPriority)) == out["priorities"]


def test_no_trend_without_ingested_history(db):
    """Right after installation (or downtime) the earlier days are empty: a 24h burst must not read as 'getting worse'."""
    for i in range(12):
        add_cluster(db, {"UKR", "RUS"}, "UKR", hours_ago=60 + i, domains=(f"o{i}.com", "x.com", "y.com"))
    db.commit()
    CrisisEngine(db, now=FIXTURE_NOW - timedelta(hours=50)).run()
    db.commit()
    crisis = db.scalar(select(Crisis))
    for i in range(20):
        c = add_cluster(db, {"UKR", "RUS"}, "UKR", hours_ago=1 + i % 10, domains=(f"n{i}.com", "x.com", "y.com"))
        db.add(CrisisEvent(crisis_id=crisis.id, event_cluster_id=c.id, match_score=1.0, reason={}))
    db.commit()
    assert score_crisis(db, crisis, now=FIXTURE_NOW).direction == "stable"  # no ingested batches recorded
    ingested_history(db)
    assert score_crisis(db, crisis, now=FIXTURE_NOW).direction == "escalating"
