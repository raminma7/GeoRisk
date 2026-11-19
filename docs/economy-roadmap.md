# Economic Intelligence roadmap (Phases 5–7)

## Already in place
- Tables: `economic_indicator_definitions`, `economic_observations` (unit, currency, seasonal adjustment, period, publication date,
  availability date, vintage, quality status, NULL for missing values), `economic_data_revisions`, `economic_data_releases`,
  `trade_flows`, `commodity_observations`, `economic_events`, `economic_analyses`, `cross_domain_links`.
- Connector interfaces with release-cadence metadata (`app/economy/connectors.py`) for World Bank, IMF, OECD, FRED/ALFRED and
  UN Comtrade.
- Canonical indicator definitions that include an **aggregation method** (e.g. inflation uses a GDP-weighted mean or median and
  is never summed).
- API routes (`/api/v1/economy/*`) returning `501 planned`, the Economy navigation entry marked *planned*, and a disabled Economy
  tab on country pages.

## Phase 5 — Economic foundation
1. Implement `WorldBankConnector.fetch_series` (WDI, annual), storing every pull as a vintage. Changed values become a new
   observation plus an `economic_data_revisions` row.
2. Add IMF (WEO/IFS) and OECD connectors. Schedule each series by its release calendar, not every 15 minutes.
3. Normalize units and currencies. Allow comparisons only for matching definition, unit and frequency (enforce this in the
   API layer).
4. Serve `GET /economy/countries/{code}` from stored observations, separating official, provisional, estimate and forecast values.

## Phase 6 — Complete economic intelligence
Dashboards, comparisons, trade analysis, economic alerts (anomaly versus each series' own history), AI economic reports using the
same evidence and validation pipeline, and economic ML datasets with vintage-aware features.

## Phase 7 — Combined intelligence
Time-windowed `cross_domain_links` (for example, a crisis window paired with an indicator series), event studies with explicit
methodology, and combined reports. **No causal claims** without an identification strategy; the default wording is "observed
during" or "associated in time". Enable `FEATURE_COMBINED_ANALYSIS` only once these pieces exist.
