"""Structured output contract for every AI analysis (validated with Pydantic).

The same JSON schema is sent to every provider (Anthropic structured outputs,
OpenAI ``json_schema`` strict mode) so analyses are provider-independent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "geo-analysis-v1"

Strength = Literal["none", "weak", "moderate", "strong"]
Confidence = Literal["low", "medium", "high"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActorEntry(_M):
    name: str
    iso3: str | None = Field(description="ISO 3166 alpha-3 if a country/government, else null")
    role: str = Field(description="role in the situation, as documented by the evidence")


class EvidencedItem(_M):
    description: str
    evidence_ids: list[str] = Field(description="IDs from the evidence list, e.g. E123 or A45")


class SignalClassification(_M):
    diplomatic: Strength
    escalation: Strength
    de_escalation: Strength
    rationale: str
    evidence_ids: list[str]


class EvidenceQuality(_M):
    level: Confidence
    rationale: str


class AnalysisOutput(_M):
    summary: str = Field(description="3-6 sentence neutral summary of what the evidence documents")
    countries_and_actors: list[ActorEntry]
    important_documented_events: list[EvidencedItem]
    recent_changes: list[EvidencedItem]
    supporting_evidence: list[EvidencedItem]
    conflicting_information: list[EvidencedItem]
    unverified_claims: list[EvidencedItem]
    de_escalation_developments: list[EvidencedItem]
    missing_information: list[str]
    signal_classification: SignalClassification
    historical_comparison: str = Field(description="brief comparison with the provided historical context, or 'insufficient data'")
    evidence_quality: EvidenceQuality
    source_references: list[str] = Field(description="evidence IDs actually relied upon")


def _strictify(schema: dict) -> dict:
    """Make every object strict (all properties required, no extras) for structured-output engines."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return walk(dict(defs[name]))
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def output_json_schema() -> dict:
    return _strictify(AnalysisOutput.model_json_schema())
