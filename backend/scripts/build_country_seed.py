"""Generate app/registry/data/countries.json (static reference data).

Inputs:
* ISO 3166 codes, UN M49 sub-regions, continents, Gleditsch-Ward codes and
  group memberships from the ``country_converter`` package data file
  (factual reference data; install with ``pip install country_converter``).
* FIPS 10-4 -> ISO mapping (used by GDELT *Geo_CountryCode fields), maintained
  explicitly below because FIPS and ISO alpha-2 frequently collide
  (e.g. FIPS ``AU`` = Austria, ISO ``AU`` = Australia).
* CAMEO country codes that differ from ISO alpha-3.

Run:  python scripts/build_country_seed.py
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

FIPS_TO_ISO2 = {
    "AF": "AF", "AL": "AL", "AG": "DZ", "AQ": "AS", "AN": "AD", "AO": "AO", "AV": "AI", "AY": "AQ",
    "AC": "AG", "AR": "AR", "AM": "AM", "AA": "AW", "AS": "AU", "AU": "AT", "AJ": "AZ", "BF": "BS",
    "BA": "BH", "BG": "BD", "BB": "BB", "BO": "BY", "BE": "BE", "BH": "BZ", "BN": "BJ", "BD": "BM",
    "BT": "BT", "BL": "BO", "BK": "BA", "BC": "BW", "BV": "BV", "BR": "BR", "IO": "IO", "VI": "VG",
    "BX": "BN", "BU": "BG", "UV": "BF", "BM": "MM", "BY": "BI", "CB": "KH", "CM": "CM", "CA": "CA",
    "CV": "CV", "CJ": "KY", "CT": "CF", "CD": "TD", "CI": "CL", "CH": "CN", "KT": "CX", "CK": "CC",
    "CO": "CO", "CN": "KM", "CG": "CD", "CF": "CG", "CW": "CK", "CS": "CR", "IV": "CI", "HR": "HR",
    "CU": "CU", "UC": "CW", "CY": "CY", "EZ": "CZ", "DA": "DK", "DJ": "DJ", "DO": "DM", "DR": "DO",
    "EC": "EC", "EG": "EG", "ES": "SV", "EK": "GQ", "ER": "ER", "EN": "EE", "WZ": "SZ", "ET": "ET",
    "FK": "FK", "FO": "FO", "FJ": "FJ", "FI": "FI", "FR": "FR", "FG": "GF", "FP": "PF", "FS": "TF",
    "GB": "GA", "GA": "GM", "GZ": "PS", "GG": "GE", "GM": "DE", "GH": "GH", "GI": "GI", "GR": "GR",
    "GL": "GL", "GJ": "GD", "GP": "GP", "GQ": "GU", "GT": "GT", "GK": "GG", "GV": "GN", "PU": "GW",
    "GY": "GY", "HA": "HT", "HM": "HM", "VT": "VA", "HO": "HN", "HK": "HK", "HU": "HU", "IC": "IS",
    "IN": "IN", "ID": "ID", "IR": "IR", "IZ": "IQ", "EI": "IE", "IM": "IM", "IS": "IL", "IT": "IT",
    "JM": "JM", "JA": "JP", "JE": "JE", "JO": "JO", "KZ": "KZ", "KE": "KE", "KR": "KI", "KN": "KP",
    "KS": "KR", "KV": "XK", "KU": "KW", "KG": "KG", "LA": "LA", "LG": "LV", "LE": "LB", "LT": "LS",
    "LI": "LR", "LY": "LY", "LS": "LI", "LH": "LT", "LU": "LU", "MC": "MO", "MK": "MK", "MA": "MG",
    "MI": "MW", "MY": "MY", "MV": "MV", "ML": "ML", "MT": "MT", "RM": "MH", "MB": "MQ", "MR": "MR",
    "MP": "MU", "MF": "YT", "MX": "MX", "FM": "FM", "MD": "MD", "MN": "MC", "MG": "MN", "MJ": "ME",
    "MH": "MS", "MO": "MA", "MZ": "MZ", "WA": "NA", "NR": "NR", "NP": "NP", "NL": "NL", "NC": "NC",
    "NZ": "NZ", "NU": "NI", "NG": "NE", "NI": "NG", "NE": "NU", "NF": "NF", "CQ": "MP", "NO": "NO",
    "MU": "OM", "PK": "PK", "PS": "PW", "PM": "PA", "PP": "PG", "PA": "PY", "PE": "PE", "RP": "PH",
    "PC": "PN", "PL": "PL", "PO": "PT", "RQ": "PR", "QA": "QA", "RE": "RE", "RO": "RO", "RS": "RU",
    "RW": "RW", "TB": "BL", "SH": "SH", "SC": "KN", "ST": "LC", "RN": "MF", "SB": "PM", "VC": "VC",
    "WS": "WS", "SM": "SM", "TP": "ST", "SA": "SA", "SG": "SN", "RI": "RS", "SE": "SC", "SL": "SL",
    "SN": "SG", "NN": "SX", "LO": "SK", "SI": "SI", "BP": "SB", "SO": "SO", "SF": "ZA", "SX": "GS",
    "OD": "SS", "SP": "ES", "CE": "LK", "SU": "SD", "NS": "SR", "SV": "SJ", "SW": "SE", "SZ": "CH",
    "SY": "SY", "TW": "TW", "TI": "TJ", "TZ": "TZ", "TH": "TH", "TT": "TL", "TO": "TG", "TL": "TK",
    "TN": "TO", "TD": "TT", "TS": "TN", "TU": "TR", "TX": "TM", "TK": "TC", "TV": "TV", "UG": "UG",
    "UP": "UA", "AE": "AE", "UK": "GB", "US": "US", "UY": "UY", "UZ": "UZ", "NH": "VU", "VE": "VE",
    "VM": "VN", "VQ": "VI", "WF": "WF", "WE": "PS", "WI": "EH", "YM": "YE", "ZA": "ZM", "ZI": "ZW",
}

# CAMEO country codes differing from ISO alpha-3 (CAMEO codebook 1.1b3 + GDELT usage)
CAMEO_TO_ISO3 = {
    "ROM": "ROU", "ZAR": "COD", "TMP": "TLS", "KSV": "XKX", "KOS": "XKX", "WSB": "PSE", "GZS": "PSE",
    "SCG": "SRB", "YUG": "SRB", "BUR": "MMR", "DRC": "COD",
}

# CAMEO regional codes -> region definitions
CAMEO_REGIONS = {
    "AFR": "Africa", "ASA": "Asia", "BLK": "Balkans", "CRB": "Caribbean", "CAU": "Caucasus",
    "CFR": "Central Africa", "CAS": "Central Asia", "CEU": "Central Europe", "EIN": "East Indies",
    "EAF": "Eastern Africa", "EEU": "Eastern Europe", "EUR": "Europe", "LAM": "Latin America",
    "MEA": "Middle East", "MDT": "Mediterranean", "NAF": "North Africa", "NMR": "North America",
    "PGS": "Persian Gulf", "SCN": "Scandinavia", "SAM": "South America", "SAS": "South Asia",
    "SEA": "Southeast Asia", "SAF": "Southern Africa", "WAF": "West Africa", "WST": "The West",
}

# CAMEO known-group / IGO codes -> organizations
CAMEO_ORGANIZATIONS = {
    "UNO": ("United Nations", "igo"), "NAT": ("North Atlantic Treaty Organization", "alliance"),
    "EEC": ("European Union", "igo"), "AFU": ("African Union", "igo"), "ARL": ("Arab League", "igo"),
    "ASN": ("Association of Southeast Asian Nations", "igo"), "OPC": ("OPEC", "economic"),
    "WTO": ("World Trade Organization", "economic"), "IMF": ("International Monetary Fund", "economic"),
    "WBK": ("World Bank", "economic"), "IAE": ("International Atomic Energy Agency", "igo"),
    "GCC": ("Gulf Cooperation Council", "igo"), "OAS": ("Organization of American States", "igo"),
    "OSC": ("Organization for Security and Co-operation in Europe", "igo"),
    "CIS": ("Commonwealth of Independent States", "igo"), "ICC": ("International Criminal Court", "igo"),
    "IRC": ("International Committee of the Red Cross", "ngo"), "MSF": ("Medecins Sans Frontieres", "ngo"),
    "AMN": ("Amnesty International", "ngo"), "HRW": ("Human Rights Watch", "ngo"),
    "WHO": ("World Health Organization", "igo"), "OIC": ("Organisation of Islamic Cooperation", "igo"),
    "SCO": ("Shanghai Cooperation Organisation", "igo"), "ECW": ("ECOWAS", "economic"),
    "SAD": ("Southern African Development Community", "economic"), "APE": ("APEC", "economic"),
    "XFM": ("Interpol", "igo"),
}

# NB: G20 omitted — the source column marks all EU members (the EU is a G20 member).
GROUP_COLUMNS = {"G7": "forum", "EU27": "political", "OECD": "economic", "BRIC": "forum", "APEC": "economic",
                 "CIS": "political"}

EXTRA_ALIASES = {
    "USA": ["United States of America", "US", "U.S.", "America", "Washington"],
    "GBR": ["Britain", "Great Britain", "UK", "U.K.", "England"],
    "RUS": ["Russian Federation", "Moscow", "Kremlin"],
    "CHN": ["People's Republic of China", "Beijing", "PRC"],
    "PRK": ["North Korea", "DPRK", "Pyongyang"], "KOR": ["South Korea", "Seoul"],
    "IRN": ["Iran", "Tehran"], "ISR": ["Israel", "Tel Aviv"], "PSE": ["Palestine", "Gaza", "West Bank"],
    "UKR": ["Ukraine", "Kyiv", "Kiev"], "TWN": ["Taiwan", "Taipei"], "SYR": ["Syria", "Damascus"],
    "COD": ["DR Congo", "DRC", "Democratic Republic of Congo", "Kinshasa"], "VEN": ["Venezuela", "Caracas"],
    "SDN": ["Sudan", "Khartoum"], "SSD": ["South Sudan"], "MMR": ["Myanmar", "Burma"],
    "TUR": ["Turkey", "Türkiye", "Ankara"], "IND": ["India", "New Delhi"], "PAK": ["Pakistan", "Islamabad"],
}


def slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-").replace("--", "-")


def main() -> None:
    import country_converter

    tsv = Path(os.path.dirname(country_converter.__file__)) / "country_data.tsv"
    rows = list(csv.DictReader(tsv.open(encoding="utf-8"), delimiter="\t"))
    iso2_to_fips = {}
    for fips, iso2 in FIPS_TO_ISO2.items():
        iso2_to_fips.setdefault(iso2, []).append(fips)
    countries = []
    for r in rows:
        if not r["ISO3"] or len(r["ISO3"]) != 3:
            continue
        iso3 = r["ISO3"].upper()
        # coco sometimes stores a regex like "^GB$|^UK$"; first alternative is the ISO code
        iso2 = re.sub(r"[\^$]", "", r["ISO2"].split("|")[0]).upper() or None
        aliases = sorted({r["name_short"], r["name_official"], *EXTRA_ALIASES.get(iso3, [])} - {""})
        groups = [g for g in GROUP_COLUMNS if r.get(g, "").strip()]
        countries.append({
            "iso3": iso3,
            "iso2": iso2,
            "iso_numeric": int(r["ISOnumeric"]) if r["ISOnumeric"].strip().isdigit() else None,
            "name": r["name_short"],
            "official_name": r["name_official"] or None,
            "continent": r["continent"] or None,
            "subregion": r["UNregion"] or None,
            "gw_code": r["GWcode"].strip() or None,
            "fips": sorted(iso2_to_fips.get(iso2 or "", [])),
            "is_historical": bool(r.get("obsolete", "").strip()),
            "aliases": aliases,
            "alias_regex": r["regex"],
            "groups": groups,
        })
    out = {
        "_provenance": "Generated by scripts/build_country_seed.py from country_converter reference data "
                       "(ISO 3166, UN M49, Gleditsch-Ward) plus explicit FIPS 10-4 and CAMEO mappings.",
        "countries": countries,
        "cameo_to_iso3": CAMEO_TO_ISO3,
        "cameo_regions": CAMEO_REGIONS,
        "cameo_organizations": {k: {"name": v[0], "kind": v[1]} for k, v in CAMEO_ORGANIZATIONS.items()},
        "groups": GROUP_COLUMNS,
    }
    dest = Path(__file__).resolve().parents[1] / "app" / "registry" / "data" / "countries.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(countries)} countries to {dest}")


if __name__ == "__main__":
    main()
