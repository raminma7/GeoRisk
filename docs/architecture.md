# Architecture

## Data flow

```
 GDELT lastupdate.txt ──► plan 15-min batches ──► download ──► raw_geopolitical_events (immutable, unique GLOBALEVENTID)
                                                     │
                                                     ▼
                    normalize (registry, roles, CAMEO) ──► normalized_geopolitical_events + event_country_roles
                                                     │                 + source_documents (URL metadata)
                                                     ▼
                                               event_clusters (dedup)
 GDELT DOC API ──► news_articles (metadata) ──► news clusters
                                                     │
                                                     ▼
                         crisis engine ──► crises, crisis_participants, crisis_events, crisis_evidence_log
                                                     │
                       indicators ──► geopolitical_indicators, country_relationships (graph edges)
                                                     │
                     priority engine ──► monitoring_priorities, crisis_snapshots ──► alerts ──► notifications
                                                     │
                       AI (optional) ──► geopolitical_analyses
 UCDP GED (versioned) ──► ucdp_events ──► outcome_labels ──► training_datasets / model_versions / model_evaluations
                       feature snapshots (point-in-time) ──► feature_snapshots
```

## Time semantics

Every observation carries the times needed for honest historical analysis:

| Field | Meaning |
|---|---|
| `event_date` | when the event reportedly happened (GDELT `SQLDATE`; UCDP `date_start`) |
| `available_at` | when the source published the record (GDELT `DATEADDED`; UCDP release date) |
| `ingested_at` | when GeoRisk stored it |
| `dataset_version_id` | release the record came from (UCDP; future economic vintages) |

Features always filter on availability, not event date.

## Reliability

- **Checkpoints and batches:** `ingestion_checkpoints` marks how far planning has reached. `ingestion_batches` holds every expected
  15-minute file, with its status (`pending`, `done`, `failed`, `missing_upstream`), attempt count, checksum and error. Missed
  intervals stay visible until recovered, and permanently missing GDELT files stop being retried after 3 attempts.
- **Idempotency:** unique constraints combined with `ON CONFLICT DO NOTHING` at every level (raw, normalized, roles, clusters,
  articles, UCDP, snapshots, labels, alerts).
- **Retries:** HTTP calls use tenacity exponential backoff with jitter. Celery tasks retry with backoff, and each runs under a
  Redis lock (`SET NX EX`) so duplicates are skipped.
- **Observability:** structlog JSON logs carry a request ID. `ingestion_runs` records each run, the worker heartbeat is tracked,
  and `/ready` checks dependencies. The Monitoring Status panel shows stale or failed sources and queued recoveries.

## Security

- API keys are role-scoped (viewer, analyst, admin), compared in constant time, configured only in the environment, and never
  returned by the API.
- Admin changes, reviews and exports are recorded in the audit log.
- Rate limiting is backed by Redis. Pydantic validates all input, with explicit bounds on list sizes (≤25 countries per comparison).
- The frontend renders external text as text only; map popups use `textContent`.
- Licensing-aware storage: no article full text is stored, and exports and AI calls check `source_permissions`.

## Extending to economics

The economy module (`app/economy`) shares the registry (ISO3, groups, regions), provenance (`data_sources`, `dataset_versions`,
`source_permissions`) and ingestion bookkeeping. Its tables model vintages and revisions explicitly. `cross_domain_links` stores
time-windowed *associations* between crises, events and indicators, never causal claims. API routes and navigation already exist
and return `501 planned`; `FEATURE_COMBINED_ANALYSIS` stays off until economic data exists.
