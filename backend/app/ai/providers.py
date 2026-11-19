"""Provider-independent LLM interface with Anthropic and OpenAI implementations.

When no provider/key is configured, :func:`get_provider` returns ``None`` and
callers report ``status: unavailable`` — the platform never fabricates AI output.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.runtime_settings import get_value

log = get_logger(__name__)


class AIError(Exception):
    pass


class AIRefusal(AIError):
    pass


@dataclass
class AIResult:
    data: dict
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class AIProvider(Protocol):
    name: str
    model: str

    def generate_json(self, system: str, user: str, schema: dict, max_tokens: int) -> AIResult: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "medium", fallbacks: bool = True):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks

    def generate_json(self, system: str, user: str, schema: dict, max_tokens: int) -> AIResult:
        a = self._anthropic
        t0 = time.monotonic()
        kwargs = dict(
            model=self.model,
            max_tokens=max(max_tokens, 16000),
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            if self.fallbacks:
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                resp = self.client.messages.create(**kwargs)
        except a.RateLimitError as e:
            raise AIError(f"rate limited: {e.message}") from e
        except a.APIStatusError as e:
            raise AIError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise AIError(f"connection error: {e}") from e
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise AIRefusal(f"model declined (category={cat})")
        if resp.stop_reason == "max_tokens":
            raise AIError("output truncated at max_tokens")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise AIError("no text output")
        return AIResult(json.loads(text), resp.model, resp.usage.input_tokens, resp.usage.output_tokens,
                        int((time.monotonic() - t0) * 1000))


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        import openai

        self._openai = openai
        self.client = openai.OpenAI(api_key=api_key, max_retries=3)
        self.model = model

    def generate_json(self, system: str, user: str, schema: dict, max_tokens: int) -> AIResult:
        o = self._openai
        t0 = time.monotonic()
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "geo_analysis", "schema": schema, "strict": True}},
            )
        except o.APIStatusError as e:
            raise AIError(f"API error {e.status_code}: {e.message}") from e
        except o.APIConnectionError as e:
            raise AIError(f"connection error: {e}") from e
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise AIRefusal(choice.message.refusal)
        if choice.finish_reason == "length":
            raise AIError("output truncated at max tokens")
        usage = resp.usage
        return AIResult(json.loads(choice.message.content or "{}"), resp.model,
                        usage.prompt_tokens if usage else None, usage.completion_tokens if usage else None,
                        int((time.monotonic() - t0) * 1000))


_override: AIProvider | None = None


def set_provider_override(p: AIProvider | None) -> None:
    """Testing hook: inject a provider (tests only)."""
    global _override
    _override = p


def get_provider(session: Session | None = None) -> AIProvider | None:
    if _override is not None:
        return _override
    s = get_settings()
    which = get_value(session, "ai_provider")
    if which == "anthropic" and s.anthropic_api_key and s.anthropic_api_key.get_secret_value():
        return AnthropicProvider(s.anthropic_api_key.get_secret_value(), s.anthropic_model, s.anthropic_effort,
                                 s.anthropic_fallbacks)
    if which == "openai" and s.openai_api_key and s.openai_api_key.get_secret_value():
        return OpenAIProvider(s.openai_api_key.get_secret_value(), s.openai_model)
    return None


def provider_status(session: Session | None = None) -> dict:
    s = get_settings()
    which = get_value(session, "ai_provider")
    configured = get_provider(session) is not None
    reason = None
    if not configured:
        reason = ("AI_PROVIDER is 'none'" if which == "none"
                  else f"AI_PROVIDER='{which}' but its API key is not set")
    return {"provider": which, "configured": configured, "reason": reason,
            "model": s.anthropic_model if which == "anthropic" else s.openai_model if which == "openai" else None}
