"""CAMEO event taxonomy helpers (used to normalize GDELT classifications).

Severity is a transparent heuristic on CAMEO codes — it describes *what kind
of action was reported*, not whether it was confirmed.
"""

from __future__ import annotations

ROOT_CATEGORIES: dict[str, tuple[str, str]] = {
    # code: (slug, label)
    "01": ("public_statement", "Make public statement"),
    "02": ("appeal", "Appeal"),
    "03": ("intent_to_cooperate", "Express intent to cooperate"),
    "04": ("consult", "Consult"),
    "05": ("diplomatic_cooperation", "Engage in diplomatic cooperation"),
    "06": ("material_cooperation", "Engage in material cooperation"),
    "07": ("provide_aid", "Provide aid"),
    "08": ("yield", "Yield"),
    "09": ("investigate", "Investigate"),
    "10": ("demand", "Demand"),
    "11": ("disapprove", "Disapprove"),
    "12": ("reject", "Reject"),
    "13": ("threaten", "Threaten"),
    "14": ("protest", "Protest"),
    "15": ("force_posture", "Exhibit military posture"),
    "16": ("reduce_relations", "Reduce relations"),
    "17": ("coerce", "Coerce"),
    "18": ("assault", "Assault"),
    "19": ("fight", "Fight"),
    "20": ("mass_violence", "Use unconventional mass violence"),
}

QUAD_CLASSES = {1: "verbal_cooperation", 2: "material_cooperation", 3: "verbal_conflict", 4: "material_conflict"}

# Root-code severity (0..1) — documented heuristic, versioned with normalization.
ROOT_SEVERITY = {
    "01": 0.0, "02": 0.0, "03": 0.0, "04": 0.0, "05": 0.0, "06": 0.0, "07": 0.0, "08": 0.0, "09": 0.05,
    "10": 0.15, "11": 0.15, "12": 0.2, "13": 0.4, "14": 0.3, "15": 0.5, "16": 0.4, "17": 0.55,
    "18": 0.75, "19": 0.85, "20": 1.0,
}
# Sub-codes with notably higher severity
CODE_SEVERITY_OVERRIDES = {
    "138": 0.55,  # threaten with military force
    "1381": 0.6, "1382": 0.6, "1383": 0.6, "1384": 0.6, "1385": 0.6,
    "154": 0.6,   # mobilize or increase armed forces
    "155": 0.55,
    "161": 0.45, "162": 0.4, "163": 0.45, "164": 0.5,  # reduce/break relations, sanctions, expel
    "166": 0.6,   # expel or withdraw (diplomats)
    "190": 0.85, "193": 0.85, "194": 0.9, "195": 0.95, "196": 0.9,  # fight, small arms, artillery/tanks, aerial, ceasefire violation
    "200": 1.0, "201": 1.0, "202": 1.0, "203": 1.0, "204": 1.0,
}

# Codes that indicate de-escalation / conflict-reduction developments
DE_ESCALATION_PREFIXES = (
    "087",   # de-escalate military engagement (0871 truce/ceasefire, 0872 ease blockade, 0873 demobilize, 0874 retreat)
    "085",   # ease economic sanctions
    "081",   # ease administrative sanctions
    "0861", "0862", "0863",  # allow peacekeepers / international involvement
    "046",   # engage in negotiation
    "057",   # sign formal agreement
    "084",   # return, release
    "0831", "0832", "0833", "0834",
    "036",   # express intent to meet or negotiate
    "0356",  # intent to settle dispute / de-escalate
    "039",   # intent to mediate
    "043",   # host / visit for diplomatic purposes - neutral-ish but cooperative
)
DIPLOMATIC_SIGNAL_ROOTS = {"10", "11", "12", "13", "16"}  # demands, disapproval, rejections, threats, reduced relations
ESCALATION_ROOTS = {"15", "17", "18", "19", "20"}
GOVERNMENT_TYPES = {"GOV", "MIL", "COP", "JUD", "SPY", "LEG", "PTY"}
NON_STATE_ARMED_TYPES = {"REB", "INS", "SEP", "UAF", "RAD"}

ACTOR_TYPE_MAP = {
    "GOV": "government", "MIL": "military", "COP": "police", "JUD": "judiciary", "LEG": "legislature",
    "SPY": "intelligence", "OPP": "opposition", "PTY": "political_party", "REB": "rebel", "INS": "insurgent",
    "SEP": "separatist", "UAF": "armed_group", "RAD": "radical_group", "CVL": "civilian", "MED": "media",
    "BUS": "business", "CRM": "criminal", "EDU": "education", "HLH": "health", "HRI": "human_rights",
    "IGO": "igo", "NGO": "ngo", "IMG": "migrants", "REF": "refugees", "LAB": "labor", "REL": "religious",
    "ELI": "elites", "MOD": "moderate", "AGR": "agriculture", "ENV": "environment", "SET": "settlers",
    "MNC": "business", "UIS": "unidentified_state_actor", "NGM": "ngm",
}


def category(root: str | None) -> str | None:
    return ROOT_CATEGORIES.get(root or "", (None, None))[0]


def category_label(root: str | None) -> str | None:
    return ROOT_CATEGORIES.get(root or "", (None, None))[1]


def severity(event_code: str | None, root: str | None) -> float:
    if event_code:
        for n in (4, 3):
            sub = event_code[:n]
            if sub in CODE_SEVERITY_OVERRIDES:
                return CODE_SEVERITY_OVERRIDES[sub]
    return ROOT_SEVERITY.get(root or "", 0.0)


def signal_type(event_code: str | None, root: str | None, quad: int | None) -> str:
    code = event_code or ""
    if any(code.startswith(p) for p in DE_ESCALATION_PREFIXES):
        return "de_escalation"
    if root in ESCALATION_ROOTS:
        return "escalation"
    if root in DIPLOMATIC_SIGNAL_ROOTS or root == "14":
        return "diplomatic_tension"
    if quad in (1, 2):
        return "cooperation"
    return "neutral"
