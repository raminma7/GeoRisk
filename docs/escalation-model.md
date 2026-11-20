# Outlook models: "will this country get better or worse?"

> **Current version:** the models predict moves of the **crisis index** (violence, military activity, tension,
> unrest, change vs normal; see `backend/app/ml/crisis_index.py` and the README), not of deaths alone. Labels: the
> index level over the next 3 / 6 months (news of the window, annualised deaths) vs the level now. Results
> 2013–2025: worse ROC-AUC 0.808 / 0.823 (base rate 11%), better 0.820 / 0.826 (base rate 36%); a simple
> index-trend rule scores 0.31–0.45. Decision thresholds: worse 0.19 / 0.22, better 0.31 / 0.30. Most important
> inputs: the index now, violence, the 3-month change of the index, democracy, momentum, military activity,
> tension news. The sections below describe the earlier, deaths-only version and its results.

Replaces the earlier conflict-onset model (removed), so the model answers the same question the site shows.

## Question (label)
For each country and month `t`:

- **Current level:** deaths from organised violence (UCDP GED + Candidate, all types: state-based, non-state,
  one-sided) in months `t-12 … t-1`, put into the site's levels: <100 Low, 100–999 Moderate, 1,000–9,999 High,
  10,000+ Critical.
- **Label `worse_{h}m` = 1** when the deaths in months `t … t+h-1`, scaled to a year (× 12/h), put the country at least
  one level higher. `h` = 3 and 6 (two models).
- Countries already Critical cannot move up and are excluded. A label is only set when the whole window is observed.

## Data and factors (all lagged: row `t` only sees data published before `t`)
- **UCDP history:** fighting events and deaths over 1–12 months, months since last fighting, interstate fighting;
  deaths from all violence types over 1/3/12 months, the current level, the trend (last 3 vs previous 9 months), and
  clashes between armed groups / attacks on civilians over 12 months.
- **Neighbours and region:** fighting and deaths next door, number of neighbours and regional countries at war.
- **Politics:** V-Dem democracy indices and regime type; EPR ethnic exclusion (data ends 2021).
- **Economy:** World Bank income, growth, inflation, population, age structure, resource rents, unemployment (each
  with the publication lag).
- **News (GDELT 1.0 monthly):** share of violent news and its 6-month change, threats, tone, conflict/cooperation
  balance, attention, protests, diplomatic tension, and **violent news vs the country's normal, corrected for the
  worldwide change** (the same idea as the site's 30-day severity step).
- Not used: global prices (they only taught the model the calendar year), live GDELT 2.0 (no history).

Yemen and Serbia were fixed before training (UCDP Gleditsch–Ward codes 678 / 345 were previously unmapped).

## Training and test
- Training rows: 76,991 country-months (3 months) / 76,470 (6 months); "got worse": 2,549 / 2,754 (3.3% / 3.6%).
- Walk-forward test 2013–2025: each year is predicted by a model trained only on rows whose whole label window ended
  before that year (no leakage of the test year's outcomes).
- Model: LightGBM (depth 3, 8 leaves, ≥100 rows per leaf, subsampling, L2 5.0, 300 trees), Platt calibration on the
  out-of-time scores. The final models are trained on all labelled rows.

| | 3 months | 6 months |
|---|---|---|
| ROC-AUC (all countries) | **0.908** | **0.902** |
| PR-AUC (base rate 3.2% / 3.4%) | 0.269 | 0.265 |
| Share of "got worse" cases in the monthly top 20 | **68%** | **64%** |
| Rule: recent trend of deaths, ROC-AUC / top 20 | 0.517 / 41% | 0.539 / 44% |
| Rule: violent news vs normal, ROC-AUC / top 20 | 0.543 / 15% | 0.537 / 14% |
| Calm now (Low → new crisis), ROC-AUC | 0.922 | 0.921 |
| Moderate → worse, ROC-AUC | 0.776 | 0.762 |
| High → Critical, ROC-AUC | 0.801 | 0.730 |

Most important factors (gain): deaths over the last 12 / 3 / 1 months, share of children, population, deaths trend,
inflation, current level, violent news, income, resource rents, unemployment.

## Reading the results
- Ranking among countries is reliable (ROC ~0.9); absolute probabilities are modest because getting worse is rare.
  The site therefore shows "× the average country" and a level.
- Calm countries close to the 100-deaths line (e.g. 70–90 deaths in the past year) rank high: a small increase is
  enough to cross into Moderate. The reasons on each row show this.
- Escalation of an existing crisis is harder to predict (ROC 0.73–0.80) than a new crisis (0.92).
- Model selection used the same test years, so the numbers are slightly optimistic.

## The "getting better" models (crisis_calming_3m / _6m)
Same data, factors and method, opposite question: label `better_{h}m` = 1 when the next `h` months, scaled to a year,
put the country at least one level **lower**. Only countries at Moderate or above (12,922 / 12,808 training rows;
4,070 / 3,537 got better).

| | 3 months | 6 months |
|---|---|---|
| ROC-AUC | **0.859** | **0.849** |
| PR-AUC (base rate 24% / 20%) | 0.633 | 0.568 |
| Rule: recent trend of deaths, ROC-AUC | 0.641 | 0.631 |
| Moderate / High / Critical, ROC-AUC | 0.87 / 0.86 / 0.83 | 0.87 / 0.85 / 0.75 |

## One outlook per country
For each window the site shows **could get worse**, **could get better** or **likely to stay the same**:

- "worse" when the worse-model probability passes its threshold, "better" likewise; when both pass, the stronger one
  relative to its threshold wins; otherwise "same".
- Thresholds = best F1 on the out-of-time test predictions:
  - Worse: 0.15 (3 months) / 0.18 (6 months). Precision 26% / 28%, recall 50% / 39%.
  - Better: 0.27 / 0.26. Precision 54% / 50%, recall 75% / 71%.
- "Could get worse" is a warning, not a forecast: about 1 in 4 flagged countries really moved up a level within the
  window (against 1 in 30 on average).

Examples (data through Sep 2026):
- **Iran:** Critical, could get better. Its deaths were concentrated in January 2026 (≈19,900); about 50 a month since May.
- **Sudan:** Critical, could get better. ≈61,000 deaths in October 2025; ≈300 a month since.
- **Ukraine:** stays Critical. A steady 5,000–7,000 deaths a month.

## Commands
```bash
python -m app.cli train-escalation-models
```

```bash
python -m app.cli predict
```
