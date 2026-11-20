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

## Pages and Admin panel

The dashboard has two public pages:

- **Today** (`/`): the Global Overview (live counts and the global event map with filters), then the situations going
  on, each with a plain headline ("Armed conflict in Ukraine and Russia"), a severity (Critical / High / Moderate /
  Low), a trend, two or three reasons in plain words, how well confirmed it is and the last 7 days of violent reports.
  It also shows what changed since yesterday and which peaceful countries could see a new conflict. Clicking a
  situation or a country opens its page.
- **Early warning** (`/early-warning`): countries ranked by the model's risk of a new conflict.

The wording is rule-based (`backend/app/monitoring/plain.py`, API `GET /api/v1/today`); no AI key is needed.

Severity of a country (Critical / High / Moderate / Low) comes from the **crisis index** (0–100,
`backend/app/ml/crisis_index.py`), built from five dimensions, not deaths alone:

| Dimension | From |
|---|---|
| Violence | verified deaths (UCDP, 12 months) + share of news about fighting and attacks |
| Military activity | share of news about forces, blockades, coercion, cut relations |
| Tension | share of news about threats, demands, sanctions, diplomatic disputes |
| Unrest | share of news about protests |
| Change vs normal | conflict news in the last 30 days vs the country's own previous year, corrected for the worldwide change |

News dimensions are shares of the country's own news, so heavy coverage alone does not raise a country. They are
pulled towards the world average when a country has little news, and expressed as percentiles against all
country-months since 1990. The strongest dimension sets the base and the others add to it (default weights
40/20/20/10/10, editable in Admin as `crisis_index_weights`). Levels are cut-offs on the historical distribution:
Critical = top 3% of country-months, High = next 7%, Moderate = next 15%. News is read from the GDELT daily files of
the last 30 days, so the index is current even though UCDP lags by about two months.

**Admin** (`/admin`): sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD` (see `.env.example`; default password
`georisk-admin`). The panel holds Settings (watchlist, thresholds, news queries, AI provider), data-collection health
and **Change password**. A changed password is stored only as a hash in the database and replaces the `.env` value;
changing it signs out other sessions. Settings changes always need this sign-in.

## Early warning (ML): "better or worse?"
A separate **model service** ([`model-service/`](model-service)) serves four models: `crisis_escalation_3m/_6m`
("could it get worse?") and `crisis_calming_3m/_6m` ("could it get better?"). For every country they estimate the
chance of moving **up** or **down** at least one level of the **crisis index** (the same Low / Moderate / High /
Critical as the site) in the next 3 or 6 months. Each country gets one
outlook per window: **could get worse**, **could get better** or **likely to stay the same** (a direction is shown
when its probability passes a threshold chosen on the test years). Tested on 2013–2025 with models that never saw the
test year: "worse" ROC-AUC 0.81 / 0.82, "better" 0.82 / 0.83 (simple trend rule: 0.31–0.45). Predictions start from
today's index (news up to today). Because verified deaths lag about two months, the outlook is compared with this
week's news: when they disagree (e.g. "could get better" while fighting is picking up), the outlook is kept and a line
says what history shows (sudden flare-ups of fighting news faded within 3 months in 64% of past cases). The outlook appears on the
**Early warning** page (filters: all / worse / better / same), on every country card and on country pages. Details:
[`docs/escalation-model.md`](docs/escalation-model.md).

**It sets itself up.** The trained model ships with the model service. On first start the background worker
imports UCDP history, downloads the country factors (World Bank, V-Dem, EPR, prices) and the last two years of the
GDELT news archive, then calculates predictions. This takes about 15–30 minutes, and the Early warning page shows
progress meanwhile. After that it updates monthly. Each month it also checks past predictions against what happened
(track record) and retrains the models, keeping a new model only if it tests at least as well. The admin page shows
both. It also keeps a daily snapshot of every country for future training. Manual commands:

```bash
docker compose exec api python -m app.cli setup-early-warning
```

```bash
docker compose exec api python -m app.cli download-factors --refresh
```

To retrain from scratch, you need the full news archive first (1988→, about 1–3 hours, resumable). Then a new model
version appears in the shared volume and the service prefers it:

```bash
docker compose exec api python -m app.cli backfill-news-history --start 1988 --end 2026
```

```bash
docker compose exec api python -m app.cli train-escalation-models
```

```bash
curl -X POST localhost:8100/reload
```

Hosts used: data.gdeltproject.org, ucdp.uu.se, api.worldbank.org, ourworldindata.org, icr.ethz.ch,
fred.stlouisfed.org.

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
