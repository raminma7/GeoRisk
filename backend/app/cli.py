"""GeoRisk command line.

    python -m app.cli seed                      # registry + data sources
    python -m app.cli ingest-gdelt [--max N]    # one live GDELT cycle (plan + recover)
    python -m app.cli backfill-gdelt --start 2026-11-18T00:00 --end 2026-11-18T06:00
    python -m app.cli ingest-news               # one GDELT DOC discovery cycle
    python -m app.cli pipeline                  # clustering -> crises -> priorities -> alerts
    python -m app.cli demo --hours 6            # real-data end-to-end demo
    python -m app.cli import-ucdp [--version 26.1] [--min-year 2015]
    python -m app.cli snapshots [--date 2026-11-18]
    python -m app.cli backfill-news-history --start 1988 --end 2025   # GDELT 1.0 archive -> country-month news
    python -m app.cli train-onset-model                              # clean labels -> model package
    python -m app.cli predict-onsets                                 # model service -> ml_predictions
    python -m app.cli labels [--horizon 1] [--start 2010-01-01]
    python -m app.cli train-baseline
    python -m app.cli analyze-crisis CRISIS_ID
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger

log = get_logger("cli")


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def cmd_seed(_args) -> dict:
    from app.ingestion.sources import seed_sources
    from app.registry.service import seed_registry

    with session_scope() as s:
        stats = seed_registry(s)
        seed_sources(s)
    return stats


def cmd_ingest_gdelt(args) -> dict:
    from app.ingestion.connectors.gdelt_events import GdeltEventsConnector

    with session_scope() as s:
        return GdeltEventsConnector(s).run(max_batches=args.max)


def cmd_backfill_gdelt(args) -> dict:
    from app.ingestion.connectors.gdelt_events import GdeltEventsConnector

    with session_scope() as s:
        c = GdeltEventsConnector(s)
        planned = c.plan_range(_dt(args.start), _dt(args.end))
        stats = c.process_pending(args.max)
        return {"planned": planned, **stats}


def cmd_ingest_news(args) -> dict:
    from app.ingestion.connectors.gdelt_doc import GdeltDocConnector

    with session_scope() as s:
        return GdeltDocConnector(s).run(max_queries=args.max_queries)


def cmd_pipeline(_args) -> dict:
    from app.workers.pipeline import run_monitoring_pipeline

    return run_monitoring_pipeline()


def cmd_demo(args) -> dict:
    """Real-data demonstration: backfill recent GDELT, discover news, run the pipeline."""
    from app.ingestion.connectors.gdelt_events import GdeltEventsConnector, floor_slot

    out: dict = {"seed": cmd_seed(args)}
    with session_scope() as s:
        c = GdeltEventsConnector(s)
        latest, _, _ = c.latest_available()
        start = floor_slot(latest - timedelta(hours=args.hours))
        out["backfill_planned"] = c.plan_range(start, latest)
        c.plan(latest)
        out["ingest"] = c.process_pending(max_batches=int(args.hours * 4) + 4)
    if not args.skip_news:
        out["news"] = cmd_ingest_news(argparse.Namespace(max_queries=args.news_queries))
    out["pipeline"] = cmd_pipeline(args)
    return out


def cmd_import_ucdp(args) -> dict:
    from app.ingestion.connectors.ucdp import UcdpGedConnector

    with session_scope() as s:
        released = _dt(args.released_at) if args.released_at else None
        return UcdpGedConnector(s).import_version(args.version, min_year=args.min_year, path=args.path,
                                                  released_at=released)


def cmd_snapshots(args) -> dict:
    from app.ml.features import build_daily_snapshots

    with session_scope() as s:
        as_of = _dt(args.date) if args.date else None
        return build_daily_snapshots(s, as_of=as_of)


def cmd_backfill_news_history(args) -> dict:
    from app.ml.news_history import backfill

    return backfill(args.start, args.end, workers=args.workers, max_files=args.max_files)


def cmd_train_onset_model(args) -> dict:
    from app.ml.onset_model import train_and_export

    with session_scope() as s:
        r = train_and_export(s, variant=args.variant)
    return {k: v for k, v in r.items() if k != "results"} | {"test": r["results"][r["variant"]]}


def cmd_predict_onsets(_args) -> dict:
    from app.ml.predictions import run_predictions

    with session_scope() as s:
        return run_predictions(s)


def cmd_labels(args) -> dict:
    from app.ml.labels import store_labels

    with session_scope() as s:
        return store_labels(s, horizon=args.horizon, start=_dt(args.start).date() if args.start else None)


def cmd_train_baseline(args) -> dict:
    from app.ml.baseline import train_ucdp_baseline

    with session_scope() as s:
        return train_ucdp_baseline(s, label_months=args.horizon)


def cmd_analyze_crisis(args) -> dict:
    from app.ai.service import analyze_crisis

    with session_scope() as s:
        a = analyze_crisis(s, args.crisis_id, trigger="on_demand")
        return {"id": a.id, "status": a.status, "provider": a.provider, "error": a.error} if a else {"status": "unavailable"}


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(prog="georisk")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    x = sub.add_parser("ingest-gdelt")
    x.add_argument("--max", type=int, default=None)
    x.set_defaults(fn=cmd_ingest_gdelt)
    x = sub.add_parser("backfill-gdelt")
    x.add_argument("--start", required=True)
    x.add_argument("--end", required=True)
    x.add_argument("--max", type=int, default=500)
    x.set_defaults(fn=cmd_backfill_gdelt)
    x = sub.add_parser("ingest-news")
    x.add_argument("--max-queries", type=int, default=None)
    x.set_defaults(fn=cmd_ingest_news)
    sub.add_parser("pipeline").set_defaults(fn=cmd_pipeline)
    x = sub.add_parser("demo")
    x.add_argument("--hours", type=float, default=6)
    x.add_argument("--skip-news", action="store_true")
    x.add_argument("--news-queries", type=int, default=None)
    x.set_defaults(fn=cmd_demo)
    x = sub.add_parser("import-ucdp")
    x.add_argument("--version", default=None)
    x.add_argument("--min-year", type=int, default=None)
    x.add_argument("--path", default=None, help="local GED CSV/zip instead of downloading")
    x.add_argument("--released-at", default=None, help="release timestamp (ISO) when importing a local file")
    x.set_defaults(fn=cmd_import_ucdp)
    x = sub.add_parser("snapshots")
    x.add_argument("--date", default=None)
    x.set_defaults(fn=cmd_snapshots)
    x = sub.add_parser("backfill-news-history")
    x.add_argument("--start", type=int, default=1988)
    x.add_argument("--end", type=int, default=2025)
    x.add_argument("--workers", type=int, default=4)
    x.add_argument("--max-files", type=int, default=None)
    x.set_defaults(fn=cmd_backfill_news_history)
    x = sub.add_parser("train-onset-model")
    x.add_argument("--variant", default=None)
    x.set_defaults(fn=cmd_train_onset_model)
    sub.add_parser("predict-onsets").set_defaults(fn=cmd_predict_onsets)
    x = sub.add_parser("labels")
    x.add_argument("--horizon", type=int, default=1)
    x.add_argument("--start", default=None)
    x.set_defaults(fn=cmd_labels)
    x = sub.add_parser("train-baseline")
    x.add_argument("--horizon", type=int, default=1)
    x.set_defaults(fn=cmd_train_baseline)
    x = sub.add_parser("analyze-crisis")
    x.add_argument("crisis_id", type=int)
    x.set_defaults(fn=cmd_analyze_crisis)
    args = p.parse_args()
    print(json.dumps(args.fn(args), default=str, indent=2))


if __name__ == "__main__":
    main()
