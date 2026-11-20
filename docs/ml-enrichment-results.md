# Enriched conflict model — results (2026-11-19)

> **Superseded.** The conflict-onset model described here has been removed and replaced by the escalation models
> ("could this country get worse in the next 3 / 6 months?"), see [escalation-model.md](escalation-model.md). The
> enrichment experiments below are kept for reference.

## What changed
The training table went from **16 columns** (UCDP fighting history only, 124 countries) to **102 columns** for
**176 countries** (every V-Dem country, not only countries that ever appear in UCDP): 77,957 country-months,
1989-02 → 2025-12. Every value uses only information available **before** the month it predicts.

| Group | Columns | Source | Lag rule |
|---|---|---|---|
| Fighting history | events/deaths last 1/3/6/12 months, months since last fighting, non-state & one-sided violence | UCDP GED 26.1 | months < t |
| Neighbours & region | neighbours' fighting (last month, 12 months), deaths, number of neighbours at war, sub-region conflict | UCDP + Natural Earth borders | months < t |
| Politics | electoral & liberal democracy, regime type, democracy change, excluded ethnic groups & population share | V-Dem (via OWID), EPR 2021 | V-Dem: Y-1 from April; EPR: Y-1 (ends 2021) |
| Economy | log GDP per capita, growth, inflation, log population, share aged 0–14, resource rents, unemployment | World Bank WDI | Y-1 from July |
| Prices | Brent oil, food index, wheat (level and 3-month change) | FRED / IMF | month t-1 |
| News | share of world coverage, violent/threat/protest/tension/de-escalation shares, Goldstein, tone, 3-month coverage change | GDELT 1.0 archive (4,739 files, 1988–2025) | month t-1 |
| **Dynamics** | change over **1, 3 and 6 months** of every monthly signal (news, neighbours, violence, prices) and 12-month change of yearly ones | derived | from already-lagged values |

Coverage: 2.8% of values empty on average. Emptiest columns: EPR (14.9% — dataset ends 2021), WDI inflation, unemployment and
resource rents for fragile states. Missing values stay empty (NaN), never 0.
**Not added:** coups (Powell & Thyne). The download server's TLS certificate chain fails verification, and verification was not
disabled.

## Results
Walk-forward: train on all years before the test year, calibrate on the previous year, test on the unseen year. Metrics are
pooled across test years, with 95% bootstrap confidence intervals.

### A. All country-months (2019–2025)
Ongoing wars dominate this set, so everything scores about the same (PR-AUC ≈ 0.956–0.958).

### B. New conflicts only — dedicated onset model
Rows are countries with **no fighting in the previous 12 months**; the question is whether fighting starts this month.
Test years 2013–2025, with **93 real onsets** among ~20,000 rows (base rate 0.45%).

| Model | Features | ROC-AUC (95% CI) | PR-AUC | New conflicts in each month's top-20 list |
|---|---|---|---|---|
| A. Fighting history only | 12 | 0.717 (0.65–0.78) | 0.028 | 52% |
| **B. + neighbours, politics, economy** | 33 | **0.794 (0.74–0.84)** | 0.025 | **68%** |
| D. + prices + news | 49 | 0.766 (0.72–0.82) | 0.021 | 60% |
| E. + all 1/3/6-month dynamics | 97 | 0.790 (0.74–0.84) | 0.020 | 58% |
| G. B + 10 selected dynamics (no prices) | 43 | **0.808 (0.76–0.86)** | 0.021 | 66% |

## Conclusions
1. **Structural factors clearly help new-conflict detection:** ROC-AUC 0.72 → 0.79–0.81, and the top-20 list catches 68%
   instead of 52% of new conflicts.
2. **News and speed-of-change features add no reliable gain at this sample size.** The best model (G) includes a few
   change signals, but its edge over B is inside the confidence interval. With only ~200 training onsets, 97 features
   overfit.
3. **Global prices act as time proxies.** The food index ranked as the second most important onset feature, but it is the
   same for every country, so it mostly encodes *when*, not *where*. Prices are excluded from the recommended model.
4. **PR-AUC is low in absolute terms** (about 5× the 0.45% base rate). New conflicts are rare and hard to predict; this is
   a research early-warning signal, not a forecast.

Recommended configuration: **model G**. Next steps: coup data (once a verified source is available), elections, a sub-national
grid (PRIO-GRID) for more onset events, and comparison with published ViEWS forecasts.

Reproduce:

```bash
cd backend && python -m app.cli backfill-news-history --start 1988 --end 2025
```

```bash
cd backend && python scripts/run_enriched_experiment.py --out ./out
```

The factor files are downloaded to `/var/tmp/georisk/factors` (see `app/ml/enrichment.py` for the URLs).

---

# Update: clean "new war" labels, final model and early warning (2026-11-19)

## Clean labels (`onset-major-v1`)
The earlier "onset" label counted any single incident after 12 quiet months (e.g. NLD 2015-12, GBR 2019-04).
New definition: **no UCDP state-based deaths in the previous 24 months, then ≥ 25 deaths (UCDP best estimate) in
months t..t+2.** Smaller events are kept as `minor_incident`.

Result: 155 labelled onset months (~55 outbreaks: e.g. Ukraine 2014, Kyrgyzstan–Tajikistan 2021, Haiti 2024,
Cambodia–Thailand 2025, Venezuela 2025) and 402 minor incidents that no longer count as new wars. Full list:
[`docs/samples/onsets_list.csv`](samples/onsets_list.csv).

Fighting history now runs to **September 2026**: GED 26.1 plus the UCDP **Candidate** monthly release
(preliminary; it never replaces official data).

## Retrained from zero (walk-forward 2013–2025, 34 onset months tested)
A default LightGBM memorised the ~150 positives (out-of-time ROC ≈ 0.5). A small, heavily regularised model
(depth-2 trees, ≥ 150 rows per leaf, subsampling) generalises:

| Model | Features | ROC-AUC (95% CI) | New wars in monthly top 20 |
|---|---|---|---|
| History only | 12 | 0.73 (0.62–0.82) | 50% |
| + neighbours, politics, economy | 33 | 0.89 (0.86–0.92) | 68% |
| **+ selected change signals (final)** | 43 | **0.89 (0.85–0.93)** | **71%** |

The model type was chosen by comparing 3 configurations on the same test years, so these numbers are slightly
optimistic. Precision stays low (new wars are ~0.2% of quiet country-months): this is a watch list, not a
forecast.

## Separate model service and use in the app
- Package: `<MODEL_DIR>/conflict_onset/<version>/` (`model.txt`, `calibrator.json` (Platt, out-of-time),
  `metadata.json`), written by `python -m app.cli train-onset-model`.
- Service: [`model-service/`](../model-service) (stand-alone FastAPI: `/predict`, `/models`, `/health`, `/reload`).
- Monthly job (`ml_monthly_early_warning`, or `python -m app.cli predict-onsets`): newest UCDP Candidate data →
  news archive → factor rows → one batch call to the service → `ml_predictions`. Pages read stored results only
  (the app works with the service offline).
- UI: **Early warning** page (risk map, ranked watch list with plain-language reasons), homepage top-5 strip,
  country risk card (gauge, rank, reasons, trend), crisis "spillover risk" for uninvolved neighbours, and
  info-level alerts when a country enters the top 10. The model is not used in the crisis priority score.
