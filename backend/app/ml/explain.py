"""Plain-language labels for model factors (used to explain predictions to users)."""

from __future__ import annotations

import math

# feature -> (short label, unit formatter)
LABELS: dict[str, tuple[str, str]] = {
    "sb_events_l1": ("Fighting events last month", "int"),
    "sb_events_l3": ("Fighting events, last 3 months", "int"),
    "sb_events_l6": ("Fighting events, last 6 months", "int"),
    "sb_events_l12": ("Fighting events, last 12 months", "int"),
    "sb_deaths_l1": ("Conflict deaths last month", "int"),
    "sb_deaths_l3": ("Conflict deaths, last 3 months", "int"),
    "sb_deaths_l6": ("Conflict deaths, last 6 months", "int"),
    "sb_deaths_l12": ("Conflict deaths, last 12 months", "int"),
    "ns_events_l3": ("Clashes between armed groups (3 months)", "int"),
    "os_events_l3": ("Attacks on civilians (3 months)", "int"),
    "interstate_l12": ("Fighting between states (12 months)", "int"),
    "months_since_sb": ("Months since last fighting", "months"),
    "sb_active_months_l12": ("Months with fighting (last year)", "int"),
    "nb_count": ("Number of neighbouring countries", "int"),
    "nb_sb_events_l1": ("Fighting in neighbouring countries last month", "int"),
    "nb_sb_events_l12": ("Fighting in neighbouring countries (12 months)", "int"),
    "nb_sb_deaths_l12": ("Deaths in neighbouring countries (12 months)", "int"),
    "nb_countries_in_conflict_l12": ("Neighbours at war", "int"),
    "region_sb_events_l12": ("Fighting elsewhere in the region (12 months)", "int"),
    "region_countries_in_conflict_l12": ("Countries at war in the region", "int"),
    "vdem_electoral_democracy": ("Electoral democracy score", "score"),
    "vdem_liberal_democracy": ("Liberal democracy score", "score"),
    "vdem_regime_type": ("Regime type", "regime"),
    "vdem_electoral_democracy_change": ("Change in democracy score (1 year)", "signed_score"),
    "epr_excluded_pop_share": ("Population in excluded ethnic groups", "pct_share"),
    "epr_excluded_groups": ("Ethnic groups excluded from power", "int"),
    "epr_relevant_groups": ("Politically relevant ethnic groups", "int"),
    "wdi_log_gdp_per_capita": ("Income per person", "usd_log"),
    "wdi_gdp_growth_pct": ("Economic growth", "pct"),
    "wdi_inflation_pct": ("Inflation", "pct"),
    "wdi_log_population": ("Population", "pop_log"),
    "wdi_pop_age_0_14_pct": ("Share of children (age 0–14)", "pct"),
    "wdi_natural_resource_rents_pct_gdp": ("Natural-resource income (% of GDP)", "pct"),
    "wdi_unemployment_pct": ("Unemployment", "pct"),
    "news_violent_share_chg_6m": ("Rise in violent news (6 months)", "pp_share"),
    "news_threat_share_chg_6m": ("Rise in threats in the news (6 months)", "pp_share"),
    "news_avg_tone_chg_3m": ("Change in news tone (3 months)", "signed"),
    "news_avg_goldstein_chg_3m": ("Change in cooperation/conflict balance (3 months)", "signed"),
    "news_coverage_change_3m": ("Change in news attention (3 months)", "pct_ratio"),
    "news_violent_share": ("Share of violent news", "pct_share"),
    "nb_sb_events_l1_chg_6m": ("Change in neighbours' fighting (6 months)", "signed_int"),
    "wdi_log_gdp_per_capita_chg_12m": ("Change in income per person (1 year)", "pct_log"),
    "vdem_liberal_democracy_chg_12m": ("Change in liberal democracy (1 year)", "signed_score"),
    "epr_excluded_pop_share_chg_12m": ("Change in ethnic exclusion (1 year)", "pp_share"),
}
REGIMES = {0: "Closed autocracy", 1: "Electoral autocracy", 2: "Electoral democracy", 3: "Liberal democracy"}


def format_value(feature: str, v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "no data"
    kind = LABELS.get(feature, (feature, "num"))[1]
    if kind == "int":
        return f"{v:,.0f}"
    if kind == "signed_int":
        return f"{v:+,.0f}"
    if kind == "months":
        return "never (since 1989)" if v >= 240 else f"{v:.0f} months"
    if kind == "score":
        return f"{v:.2f} of 1"
    if kind == "signed_score":
        return f"{v:+.2f}"
    if kind == "regime":
        return REGIMES.get(int(round(v)), str(v))
    if kind == "pct":
        return f"{v:.1f}%"
    if kind == "pct_share":
        return f"{v * 100:.1f}%"
    if kind == "pp_share":
        return f"{v * 100:+.1f} pts"
    if kind == "pct_ratio":
        return f"{v * 100:+.0f}%"
    if kind == "usd_log":
        return f"${math.exp(v):,.0f}"
    if kind == "pct_log":
        return f"{(math.exp(v) - 1) * 100:+.1f}%"
    if kind == "pop_log":
        p = math.exp(v)
        return f"{p / 1e6:,.1f} million" if p >= 1e6 else f"{p:,.0f}"
    if kind == "signed":
        return f"{v:+.2f}"
    return f"{v:.3g}"


def reason(feature: str, value: float | None, effect: float) -> dict:
    label = LABELS.get(feature, (feature.replace("_", " "), "num"))[0]
    return {"factor": feature, "label": label, "value": format_value(feature, value),
            "direction": "raises" if effect > 0 else "lowers", "weight": round(abs(effect), 4)}
