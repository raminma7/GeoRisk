"""Download the external factor files used by the enriched / early-warning model.

Exactly the sources the model was trained on; files are written atomically into ``settings.factor_dir`` and
skipped when already present (use ``refresh=True`` to re-download, e.g. after new annual releases).

Hosts needed: api.worldbank.org, ourworldindata.org, icr.ethz.ch, fred.stlouisfed.org.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingestion import http

log = get_logger(__name__)

WDI_CODES = ["NY.GDP.PCAP.KD", "NY.GDP.MKTP.KD.ZG", "FP.CPI.TOTL.ZG", "SP.POP.TOTL", "SP.POP.0014.TO.ZS",
             "NY.GDP.TOTL.RT.ZS", "SL.UEM.TOTL.ZS"]
OWID = ["electoral-democracy-index", "liberal-democracy-index", "political-regime"]
FRED = ["MCOILBRENTEU", "PFOODINDEXM", "PWHEAMTUSDM"]


def sources() -> dict[str, str]:
    """file name -> URL"""
    y = date.today().year
    out = {f"wdi_{c}.json": f"https://api.worldbank.org/v2/country/all/indicator/{c}?format=json&per_page=20000&date=1985:{y}"
           for c in WDI_CODES}
    out |= {f"owid_{g}.csv": f"https://ourworldindata.org/grapher/{g}.csv?v=1&csvType=full&useColumnShortNames=true"
            for g in OWID}
    out["epr.csv"] = "https://icr.ethz.ch/data/epr/core/EPR-2021.csv"
    out |= {f"fred_{s}.csv": f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}" for s in FRED}
    return out


def factor_dir() -> Path:
    return Path(get_settings().factor_dir)


def missing() -> list[str]:
    d = factor_dir()
    return [name for name in sources() if not (d / name).exists() or (d / name).stat().st_size == 0]


def download_factors(refresh: bool = False, fetch=None) -> dict:
    """Download missing (or all, with ``refresh``) factor files. ``fetch(url) -> bytes`` is injectable for tests."""
    fetch = fetch or (lambda url: http.fetch(url).content)
    d = factor_dir()
    d.mkdir(parents=True, exist_ok=True)
    status: dict[str, str] = {}
    for name, url in sources().items():
        target = d / name
        if target.exists() and target.stat().st_size > 0 and not refresh:
            status[name] = "present"
            continue
        try:
            payload = fetch(url)
            if not payload or len(payload) < 50:
                raise ValueError("empty response")
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(payload)
            tmp.replace(target)
            status[name] = "downloaded"
        except Exception as exc:  # noqa: BLE001 - one source failing leaves its columns empty, not the whole model
            status[name] = f"failed: {type(exc).__name__}: {exc}"[:200]
            log.warning("factor_download_failed", file=name, error=status[name])
    failed = [k for k, v in status.items() if v.startswith("failed")]
    log.info("factors_ready", dir=str(d), failed=len(failed))
    return {"dir": str(d), "files": status, "failed": failed}
