# GeoRisk

**Global geopolitical & conflict intelligence platform** — automated, evidence-based monitoring of world events, with a foundation
for a future Economic Intelligence module.

GeoRisk continuously ingests real GDELT event data and news metadata, normalizes countries and actors, groups related reports,
discovers and tracks multi-country crises, ranks situations with an explainable *review-priority* engine, raises evidence-backed
alerts and keeps a full history for machine learning. The dashboard populates itself — no country selection required.

> **How to read the output**
> - GDELT events are *machine-extracted from news* and are **not independently verified**. "Multi-source reported" means ≥3
>   independent outlets reported it — still unverified. Only UCDP records are a human-coded research dataset.
> - Priority scores are **analyst review priority**, not probabilities of war.
> - AI analyses are **interpretations**, stored with provider, model, prompt version and evidence IDs. The platform works without AI.

## Contents

- [Quick start (Docker Compose)](#quick-start-docker-compose)
- [Real-data demonstration](#real-data-demonstration)
- [Local development without Docker](#local-development-without-docker)
- [Architecture](#architecture)
- [What runs automatically](#what-runs-automatically)
- [REST API](#rest-api)
- [Configuration](#configuration)
- [Data sources, licensing and unsupported integrations](#data-sources-licensing-and-unsupported-integrations)
- [Testing](#testing)
- [Known limitations](#known-limitations)
- Further docs: [`docs/architecture.md`](docs/architecture.md) · [`docs/methodology.md`](docs/methodology.md) ·
  [`docs/data-sources.md`](docs/data-sources.md) · [`docs/economy-roadmap.md`](docs/economy-roadmap.md)

## Early warning (experimental ML)
A separate **model service** ([`model-service/`](model-service)) serves a model that ranks countries without recent
conflict by their risk of a **new** conflict (≥ 25 deaths within 3 months) over the next window. It's trained on UCDP
history plus neighbours, politics, economy and news trends; out-of-time ROC-AUC is 0.89, and 7 in 10 new conflicts
(2013–2025) were in its monthly top 20. Predictions run monthly and are stored, then shown on the **Early warning**
page, the homepage, country pages and crisis pages (spillover). Details:
[`docs/ml-enrichment-results.md`](docs/ml-enrichment-results.md).

```bash
docker compose exec api python -m app.cli train-onset-model
```

```bash
docker compose exec api python -m app.cli predict-onsets
```

## Quick start (Docker Compose)

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

- Dashboard: http://localhost:3000
- API + OpenAPI docs: http://localhost:8000/docs

The `api` container applies Alembic migrations and seeds the country/actor registry on start. `beat` schedules ingestion every
15 minutes; the first GDELT cycle imports the most recent hour, after which the homepage fills automatically. To populate
immediately with several hours of history, run the demo below.

## Real-data demonstration

```bash
docker compose exec api python -m app.cli demo --hours 6
```

This downloads the last 6 hours of real GDELT 2.0 event files (24 × 15-minute batches), runs GDELT DOC news discovery, then
runs crisis discovery → indicators → priority scoring → alerts. Open http://localhost:3000.

Optional historical/ML foundation (≈420k UCDP events, ~5 minutes):

```bash
docker compose exec api python -m app.cli import-ucdp --version 26.1
```

```bash
docker compose exec api python -m app.cli labels --start 2010-01-01
```

```bash
docker compose exec api python -m app.cli train-baseline
```

Other commands: `ingest-gdelt`, `backfill-gdelt --start … --end …`, `ingest-news`, `pipeline`, `snapshots`,
`analyze-crisis ID` (see `python -m app.cli --help`).

**Verified in development (2026-11-19):** a 6-hour real backfill ingested 23,492 GDELT events (≈29.5k after live cycles) across 186
countries, produced 52 crises, 616 country-relationship edges and 41 alerts. The full UCDP GED 26.1 import loaded 417,968 events,
and the baseline model ran a 4-fold walk-forward evaluation (see Datasets & Models in the UI).

## Local development without Docker

Requirements: Python 3.12+, Node 22+, PostgreSQL 16, Redis 7.

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e "backend[dev]"
```

```bash
cd backend && alembic upgrade head && python -m app.cli seed
```

```bash
cd backend && uvicorn app.main:app --reload
```

```bash
cd backend && celery -A app.workers.celery_app worker -Q default,ai,ml --loglevel=INFO
```

```bash
cd backend && celery -A app.workers.celery_app beat --loglevel=INFO
```

```bash
cd frontend && npm install && npm run dev
```

`DATABASE_URL` defaults to `postgresql+psycopg://georisk:georisk@localhost:5432/georisk`, `REDIS_URL` to `redis://localhost:6379/0`.

## Architecture

```
backend/app/
  core/          config, DB, structured logging, API-key roles, rate limiting, runtime settings
  registry/      countries, regions, groups, organizations, actors, source-code mappings (ISO3 ⇄ CAMEO/FIPS/GW)
  ingestion/     provenance tables, HTTP (retry/backoff/rate limit), checkpoints & batches, connectors:
                 gdelt_events, gdelt_doc, ucdp  (ACLED: optional, disabled)
  geopolitics/   CAMEO taxonomy, normalization (country roles), news clustering, indicators, relationship graph
  crises/        crisis entity + discovery/tracking engine with evidence log and review flags
  monitoring/    explainable priority engine, alerts
  ai/            provider-independent interface (Anthropic, OpenAI), evidence aggregation, injection defenses
  ml/            point-in-time feature snapshots, UCDP outcome labels, exports, walk-forward baseline
  economy/       future module: schema, connector interfaces, indicator definitions (feature-flagged)
  notifications/ alert dispatch (log + optional webhook)
  api/v1/        versioned REST API
  workers/       Celery app, Beat schedule, Redis task locks, monitoring pipeline
frontend/        Next.js (App Router) + TypeScript + Tailwind + Recharts + MapLibre
```

Domains are decoupled: connectors write provenance-tagged rows; the crisis engine reads deduplicated clusters; the priority engine
reads crises; the AI layer reads evidence bundles; ML reads point-in-time snapshots. The economy module shares the registry,
provenance, dataset-version and licensing tables but has no dependency on geopolitical code. Details: `docs/architecture.md`.

## What runs automatically

| Task | Default schedule | Notes |
|---|---|---|
| GDELT event ingestion | every 15 min | plans every expected 15-min file; missed files are recovered; restarts never duplicate |
| Monitoring pipeline (crisis clustering → indicators → priorities → alerts) | after each ingestion with new data + every 15 min | alerts are event-driven |
| GDELT DOC news discovery | every 15 min | topic + watchlist queries, ≤1 request / 5 s |
| AI crisis analyses | hourly + after new alerts | only if an AI provider is configured; skipped when evidence is unchanged |
| Daily intelligence report | daily 06:17 UTC | AI provider required |
| ML feature snapshots | daily 02:07 UTC | point-in-time, immutable |
| UCDP release check | weekly | imports the configured release if absent |
| Worker heartbeat | every minute | shown in Monitoring Status |

All intervals are configurable (`.env`). Each task holds a Redis lock, so duplicate runs are skipped.

## REST API

Versioned under `/api/v1`, documented at `/docs` (OpenAPI). Main endpoints:

| Area | Endpoints |
|---|---|
| Global monitoring | `GET /overview`, `GET /monitoring/priorities`, `GET /monitoring/priorities/{crisis}/history`, `GET /monitoring/alerts`, `POST /monitoring/alerts/{id}/review`, `GET /monitoring/status` |
| Countries | `GET /countries`, `GET /countries/{code}`, `GET /countries/{code}/events?role=…`, `GET /countries/{code}/analysis`, `GET /regions` |
| Multi-country | `POST /countries/compare`, `POST /analysis/multi-country` |
| Crises | `GET /crises`, `GET /crises/{id}`, `GET /crises/{id}/timeline`, `GET /crises/{id}/analysis`, `POST /crises/{id}/review` |
| News & events | `GET /news` (`clustered=true` for deduplicated clusters), `GET /events` (`view=events|clusters|map`), `GET /events/categories` |
| AI | `POST /analysis/crises/{id}`, `POST /analysis/countries/{code}`, `GET /analysis/reports` |
| Datasets & models | `GET /datasets`, `POST /datasets/export`, `GET /datasets/{id}/download`, `GET /models` |
| Economy (planned) | `GET /economy/global`, `GET /economy/countries/{code}`, `POST /economy/compare` → `501 planned`; `GET /economy/indicators` (definitions only) |
| Settings | `GET /settings`, `PUT /settings/{key}` |
| Health | `GET /health`, `GET /ready` |

**Auth:** `X-API-Key` header with keys from `API_KEYS="admin:<key>,analyst:<key>"`. Reads are public when `PUBLIC_READ=true`.
Reviews, exports and on-demand AI runs need `analyst`; settings changes need `admin`. With no keys configured, writes are only
allowed when `ENVIRONMENT=development`. Lists use `limit`/`offset` pagination; rate limits default to 240/min (20/min for
expensive endpoints).

## Configuration

See [`.env.example`](.env.example). Highlights: `WATCHLIST`, schedule intervals, `AI_PROVIDER` (`none|anthropic|openai`) with
`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, `API_KEYS`, `FEATURE_ECONOMY`, `FEATURE_ACLED`, `ALERT_WEBHOOK_URL`. The watchlist, priority
weights and levels, crisis thresholds, DOC topic queries and the AI provider can also be changed at runtime from **Settings**
(admin, audited).

## Data sources, licensing and unsupported integrations

| Source | Status | Notes |
|---|---|---|
| GDELT Events 2.0 | **live** | free/open with citation; machine-extracted, unverified |
| GDELT DOC 2.0 API | **live** | headline/URL metadata only (no full text); not an exhaustive firehose; ≤1 req/5 s; index lags real time by hours |
| UCDP GED | **live (bulk)** | versioned imports via the public CSV downloads (the UCDP API itself needs an access token); CC BY 4.0 |
| ACLED | interface only, **disabled** | needs credentials *and* licence permissions; excluded from ML, embeddings and exports |
| Anthropic / OpenAI | **optional** | needs `AI_PROVIDER` + API key; without them, analyses report `unavailable` (never fabricated) |
| World Bank, IMF, OECD, FRED/ALFRED, UN Comtrade | **planned** (Phase 5) | connector interfaces only; FRED and Comtrade need API keys |

Every source has a `source_permissions` record that the AI layer, ML dataset builder and exporter enforce. Citations are in
`docs/data-sources.md`.

## Testing

```bash
cd backend && pytest
```

Tests need PostgreSQL (`TEST_DATABASE_URL`, default `postgresql+psycopg://georisk@localhost:5432/georisk_test`). There are 56
tests: unit tests (normalization, FIPS/CAMEO mapping, role semantics, scoring, sanitization, schema strictness, labels,
leakage-free features) and integration tests (idempotent re-ingestion, gap recovery, backfill, DOC dedup, multi-country crisis
discovery, review flagging, theater merging, priority explainability, de-escalation, alert evidence, API contracts, RBAC,
licensing-gated exports, AI with a test provider, UCDP import, point-in-time snapshots, and an Alembic-vs-models check).
Fixtures in `backend/tests/fixtures` are **small captured samples of real GDELT/UCDP data, used only by tests**.

```bash
cd frontend && npm run typecheck && npm run lint && npm run build
```

## Known limitations

- **GDELT coverage bias:** GDELT over-represents English-language and US media, so high-volume countries rank high. The priority
  engine counts distinct outlets (capped) and down-weights non-political actors, but analysts should read the explanation.
- **Baselines need history:** the change-vs-baseline component turns on after 7 days of ingested data; until then it is marked
  unavailable or uses a damped 24h comparison (shown in each explanation).
- **GDELT DOC rate limits** are shared per egress IP; failed queries are recorded and retried from their own checkpoint.
- **AI** was verified with a recorded test provider; live provider calls require your API key.
- **UCDP research mode:** historical ML features use the final coding of the imported release; live snapshots respect release dates.
- The baseline model's country set is the countries that appear in UCDP, which inflates the base rate. It is an experimental research
  baseline and is never used for monitoring or shown as a probability.
- The Docker images were authored but not built in the development sandbox (no Docker daemon); the same commands were run natively.
