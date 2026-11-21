"""LiteLLM gateway: ordered key pool with automatic failover + LiteLLM response cache.

Flow for one request:
  for each key in order (key_1, key_2, ...):
      if X-Simulate-Failure targets this key -> raise the matching LiteLLM exception
      cache lookup (LiteLLM cache, key = model + full message list) -> hit: LLM_CACHE
      live call via litellm.acompletion(api_key=...) -> success: store in cache, LIVE_INFERENCE
      any provider error (429 / quota / 5xx / timeout / auth) -> try the next key
  all keys failed -> AllProvidersUnavailable (the API turns it into a 503)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import litellm
from litellm.caching.caching import Cache

from .config import KeySlot, Settings

logger = logging.getLogger("lyftr.llm")

LIVE_INFERENCE = "LIVE_INFERENCE"
LLM_CACHE = "LLM_CACHE"

SIMULATABLE_STATUSES = {429, 500, 502, 503}

_EXCEPTIONS: dict[int, type[Exception]] = {
    429: litellm.RateLimitError,
    500: litellm.InternalServerError,
    502: litellm.BadGatewayError,
    503: litellm.ServiceUnavailableError,
}


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed for this request."""


@dataclass
class CompletionResult:
    reply: str
    resolution_source: str
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


def parse_simulation_header(value: str | None) -> dict[str, int]:
    """Parse `key_1=429,key_2=500` or `*=429` into {key_id: status}. Invalid pairs are ignored."""
    plan: dict[str, int] = {}
    if not value:
        return plan
    for pair in value.split(","):
        key_id, sep, status = pair.strip().partition("=")
        if not sep:
            continue
        try:
            code = int(status.strip())
        except ValueError:
            continue
        if code in SIMULATABLE_STATUSES and key_id.strip():
            plan[key_id.strip()] = code
    return plan


def simulated_status(plan: dict[str, int], key_id: str) -> int | None:
    return plan.get(key_id, plan.get("*"))


def _usage_value(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    return int(value or 0)


def _reply_text(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices") or []
        message = (choices[0] or {}).get("message") or {} if choices else {}
        return (message.get("content") or "").strip()
    return (response.choices[0].message.content or "").strip()


class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.keys: list[KeySlot] = settings.keys
        self.timeout = settings.request_timeout_seconds
        self.simulation_enabled = settings.enable_failure_simulation
        # LiteLLM's built-in in-memory cache. Its key is derived from the call
        # params (model + messages + sampling params); api_key is NOT part of it,
        # so a reply cached via key_1 is served even when key_1 is down.
        self.cache = Cache(type="local", default_in_memory_ttl=settings.cache_ttl_seconds)
        litellm.cache = self.cache

    @property
    def cache_enabled(self) -> bool:
        return self.cache is not None

    async def complete(
        self, messages: list[dict[str, str]], simulate_header: str | None = None
    ) -> CompletionResult:
        plan = parse_simulation_header(simulate_header) if self.simulation_enabled else {}
        failures = 0

        for slot in self.keys:
            try:
                status = simulated_status(plan, slot.key_id)
                if status is not None:
                    raise _EXCEPTIONS[status](
                        message=f"Simulated HTTP {status} via X-Simulate-Failure",
                        llm_provider="simulation",
                        model=slot.model,
                    )

                cached = await self.cache.async_get_cache(model=slot.model, messages=messages)
                if cached:
                    reply = _reply_text(cached)
                    if reply:
                        usage = cached.get("usage") if isinstance(cached, dict) else None
                        return CompletionResult(
                            reply=reply,
                            resolution_source=LLM_CACHE,
                            model=slot.model,
                            input_tokens=_usage_value(usage, "prompt_tokens"),
                            output_tokens=_usage_value(usage, "completion_tokens"),
                            key_id=None,
                            fallback_attempts=0,
                        )

                response = await litellm.acompletion(
                    model=slot.model,
                    messages=messages,
                    api_key=slot.api_key,
                    timeout=self.timeout,
                    num_retries=0,  # failover is ours: never burn time retrying a dead key
                    caching=False,  # we write to the cache explicitly below, after validation
                )
                reply = _reply_text(response)
                if not reply:
                    raise ValueError("provider returned an empty reply")

                await self.cache.async_add_cache(response, model=slot.model, messages=messages)
                return CompletionResult(
                    reply=reply,
                    resolution_source=LIVE_INFERENCE,
                    model=slot.model,
                    input_tokens=_usage_value(response.usage, "prompt_tokens"),
                    output_tokens=_usage_value(response.usage, "completion_tokens"),
                    key_id=slot.key_id,
                    fallback_attempts=failures,
                )
            except Exception as exc:  # noqa: BLE001 - any provider failure means "try the next key"
                failures += 1
                # Log the key id and error type only, never the key itself.
                logger.warning(
                    "key %s failed (%s: %s); failing over",
                    slot.key_id,
                    type(exc).__name__,
                    getattr(exc, "status_code", "n/a"),
                )

        raise AllProvidersUnavailable()
