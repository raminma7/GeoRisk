# Escalation model: "will this country's crisis get worse?"

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

## Commands
```bash
python -m app.cli train-escalation-models
```

```bash
python -m app.cli predict
```
