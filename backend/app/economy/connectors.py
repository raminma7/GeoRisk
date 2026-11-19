"""Economic data connector interfaces — FUTURE MODULE (Phase 5).

Each connector is independent and replaceable. None fetch data in the MVP;
they declare metadata (rate limits, release cadence, credentials) so
scheduling and licensing controls can be designed now. Economic series are
synchronized according to each series' release frequency, never every 15
minutes by default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class IndicatorSpec:
    indicator_key: str
    name: str
    source_code: str
    unit: str
    frequency: str  # A | Q | M
    currency: str | None = None
    price_basis: str | None = None
    seasonal_adjustment: str | None = None
    aggregation_method: str = "not_aggregatable"  # never sum rates; weighted means need weights


class EconomicConnector(ABC):
    source_key: str
    requires_credentials: bool = False
    min_request_interval_seconds: float = 1.0
    default_release_schedule: str = "per-series"

    @abstractmethod
    def fetch_series(self, indicator: IndicatorSpec, country_iso3: str, start: date, end: date) -> list[dict]:
        """Return observations with period, value (None if missing), vintage and availability timestamps."""


class _Planned(EconomicConnector):
    def fetch_series(self, indicator, country_iso3, start, end):  # noqa: D401
        raise NotImplementedError(f"{self.source_key} connector is planned for Phase 5 (Economic Foundation).")


class WorldBankConnector(_Planned):
    source_key = "world_bank"
    base_url = "https://api.worldbank.org/v2"
    default_release_schedule = "annual (WDI updates several times per year)"


class ImfConnector(_Planned):
    source_key = "imf"
    default_release_schedule = "WEO: April/October; IFS: monthly"


class OecdConnector(_Planned):
    source_key = "oecd"
    default_release_schedule = "per dataset (monthly/quarterly)"


class FredConnector(_Planned):
    source_key = "fred"
    requires_credentials = True
    default_release_schedule = "per series; ALFRED provides vintages"


class ComtradeConnector(_Planned):
    source_key = "un_comtrade"
    requires_credentials = True
    default_release_schedule = "monthly/annual as reporters submit"


CONNECTORS: dict[str, type[EconomicConnector]] = {
    c.source_key: c for c in (WorldBankConnector, ImfConnector, OecdConnector, FredConnector, ComtradeConnector)
}

# Canonical indicator definitions (metadata only — no values).
INDICATORS: list[IndicatorSpec] = [
    IndicatorSpec("gdp_current_usd", "GDP (current US$)", "NY.GDP.MKTP.CD", "USD", "A", "USD", "current", None, "sum"),
    IndicatorSpec("gdp_real_growth", "Real GDP growth (annual %)", "NY.GDP.MKTP.KD.ZG", "percent", "A", None, "constant",
                  None, "gdp_weighted_mean"),
    IndicatorSpec("inflation_cpi", "Inflation, consumer prices (annual %)", "FP.CPI.TOTL.ZG", "percent", "A",
                  aggregation_method="gdp_weighted_mean_or_median"),
    IndicatorSpec("unemployment", "Unemployment (% of labor force, ILO est.)", "SL.UEM.TOTL.ZS", "percent", "A",
                  aggregation_method="labor_force_weighted_mean"),
    IndicatorSpec("current_account_pct_gdp", "Current account balance (% of GDP)", "BN.CAB.XOKA.GD.ZS", "percent", "A"),
    IndicatorSpec("govt_debt_pct_gdp", "Central government debt (% of GDP)", "GC.DOD.TOTL.GD.ZS", "percent", "A"),
    IndicatorSpec("fdi_net_inflows_usd", "FDI, net inflows (BoP, current US$)", "BX.KLT.DINV.CD.WD", "USD", "A", "USD",
                  "current", None, "sum"),
    IndicatorSpec("reserves_usd", "Total reserves incl. gold (current US$)", "FI.RES.TOTL.CD", "USD", "A", "USD",
                  "current", None, "sum"),
    IndicatorSpec("exports_gs_usd", "Exports of goods and services (current US$)", "NE.EXP.GNFS.CD", "USD", "A", "USD",
                  "current", None, "sum"),
    IndicatorSpec("imports_gs_usd", "Imports of goods and services (current US$)", "NE.IMP.GNFS.CD", "USD", "A", "USD",
                  "current", None, "sum"),
    IndicatorSpec("energy_use_per_capita", "Energy use (kg of oil equivalent per capita)", "EG.USE.PCAP.KG.OE",
                  "kgoe_per_capita", "A", aggregation_method="population_weighted_mean"),
]
