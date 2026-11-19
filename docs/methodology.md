# Methodology

All algorithms are deterministic, versioned, and stored with their inputs so every output can be explained and reproduced.

## 1. Normalization (`gdelt-v1`)

- Actor country codes (CAMEO, mostly ISO3; explicit overrides such as `ROM→ROU`) and geo codes (FIPS 10-4, explicit table
  because FIPS and ISO collide, e.g. `AU` = Austria) map to the ISO3 registry.
- **Country roles** per event (table `event_country_roles`):
  `occurred_in` (location), `actor1_affiliation`, `actor2_affiliation`, `government_involved` (actor type GOV/MIL/COP/JUD/SPY/LEG/PTY),
  `non_state_cross_border` (REB/INS/SEP/UAF/RAD actor acting outside its affiliation country). Location never implies government
  involvement.
- CAMEO root → category; a documented **severity** heuristic (0–1, e.g. fight 0.85, mass violence 1.0, threats 0.4); a **signal
  type** (escalation / de-escalation codes such as 087x ceasefire, 085 eased sanctions, 046 negotiation / diplomatic tension /
  cooperation).
- **Deduplication:** reports with the same (event date, CAMEO root, location country, actor-country set) form one *event cluster*.
  A cluster with ≥3 distinct source domains is `multi_source_reported` (still unverified).
- Raw rows are immutable (`raw_geopolitical_events`, unique on GDELT `GLOBALEVENTID`); re-normalization writes new versioned rows.

## 2. Crisis discovery (`crisis-v1`)

1. Candidate clusters (72h lookback): conflict-coded (quad 3/4) clusters with political actors (government, military, armed groups,
   IGOs…) or ≥2 countries; cooperation/yield clusters only attach to existing crises (for de-escalation tracking).
2. Match score to an existing crisis: `0.45·containment + 0.25·jaccard + 0.30·location_match` over country sets.
   `≥ 0.55` attaches; `[0.40, 0.55)` attaches **and flags for human review**; below is left for discovery.
3. Remaining clusters are grouped by theater (location country). A crisis is created when a group has ≥6 clusters, ≥4 independent
   outlets and either ≥2 material-conflict or ≥4 diplomatic-signal clusters. Core countries are those present in ≥25% of the group.
4. Participants (countries and actors) grow as attached evidence accumulates (≥3 clusters). Locations grow too, so a crisis can span
   several countries.
5. Crises whose multi-country cores overlap (Jaccard ≥ 0.66) are merged; the merge is logged on both.
6. Lifecycle: candidate → active (≥2× creation thresholds or human confirmation) → dormant (no evidence for 10 days) → re-activated
   by new evidence. Humans can confirm, reject, resolve or retitle.

Every decision is written to `crisis_evidence_log` with the criteria values and cluster IDs.

## 3. Monitoring priority (`priority-v1`)

The score is a **review priority from 0 to 100**. It is not a probability. Components are each normalized to 0..1, then weighted (configurable):

| Component | Definition | Default weight |
|---|---|---|
| severity | 0.6 × mean of top-5 weighted material-conflict severities + 0.4 × (1 − e^(−Σ/25)); clusters without political actors count 0.35×, unconfirmed single reports 0.6× | 0.30 |
| change_vs_baseline | z-score of today's conflict clusters at the crisis locations vs. the previous 30 days (needs ≥7 days of data); otherwise a damped 24h-vs-24h comparison, or 0 with an explicit note | 0.20 |
| corroboration | log(1 + distinct outlets) / log(101) | 0.15 |
| diplomatic_signals | n / (n + 15) over demands, rejections, threats, reduced relations with political actors | 0.10 |
| evidence_completeness | share of clusters with geolocation, actor coding, multi-source status | 0.05 |
| freshness | 12-hour half-life since last evidence | 0.10 |
| new_development | new crisis (<48h) or new participant (<24h) | 0.10 |

Levels: critical ≥ 75, high ≥ 55, elevated ≥ 35. Report volume alone cannot drive the score: inflating report counts 500× changes it
by fewer than 8 points (this is tested).

**Direction:** escalating if 24h conflict intensity exceeds 1.5× the prior-72h daily average. De-escalating if it falls below 0.6×
while cooperation holds or de-escalation-coded events (e.g. ceasefire) appear.

**Evidence confidence:** 0.5 × corroboration + 0.3 × multi-source share + 0.2 × completeness → low / medium / high.

Each run is stored in `monitoring_priorities` with the components, triggering indicators, supporting cluster IDs, independent
source count, confidence, weights, algorithm version and timestamp. History is kept in `crisis_snapshots`.

## 4. Alerts

Created right after each scoring run when:
- a crisis is new (review required);
- the level crosses up into `ALERT_THRESHOLD_LEVEL` (default *high*; critical requires review);
- direction turns escalating, at elevated level or above;
- direction turns de-escalating.

Alerts are deduplicated per crisis, type and day. They embed the priority components, supporting clusters with example source links,
the source count, the confidence and a caveat.

## 5. AI analysis (`geo-analysis-v1`)

- Evidence bundle: at most 40 deduplicated clusters ranked by severity × outlets, plus one headline per news cluster, the crisis
  indicators and UCDP history. Only sources whose `allow_ai_processing` permission is set are included.
- Prompt-injection defenses: control characters are stripped, instruction-like phrases are neutralized, and lengths are capped.
  Data is passed as JSON inside `<untrusted_evidence>`, and the system prompt forbids following embedded instructions.
- Output follows a strict JSON schema (summary, actors, documented events, recent changes, supporting evidence, conflicting
  information, unverified claims, de-escalation, missing information, signal classification, historical comparison, evidence
  quality, source references). It is validated with Pydantic, and cited IDs that are not in the bundle are dropped and recorded.
- Stored with provider, model, prompt version, input hash, evidence IDs and token usage. Identical evidence is not re-sent.

## 6. ML foundation

- **Feature snapshots** (`geo-daily-v1`) for countries, country pairs, crises, regions and the world. They use only rows with
  availability ≤ `as_of` (GDELT `first_seen_at`, UCDP release date) and are immutable once written.
- **Outcome labels** (`ucdp-v1`) are defined per country-month from UCDP, never from AI or GDELT:
  `state_based_event_next_hm`, `interstate_event_next_hm`, `state_based_fatalities_next_hm`, and `escalation_next_hm`
  (≥2× and ≥25 deaths versus the previous window, given prior fatalities).
- **Baseline:** LightGBM using lagged UCDP features, with walk-forward yearly folds, isotonic calibration on the last training year,
  `scale_pos_weight` for class imbalance, a fixed seed, and dataset checksums. Results are compared with persistence and base-rate
  baselines and stored in `model_versions` / `model_evaluations` with status `experimental`.
