"""All configuration comes from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class KeySlot:
    """One entry of the key pool. `key_id` is public, `api_key` never leaves the server."""

    key_id: str
    api_key: str
    model: str


@dataclass(frozen=True)
class Settings:
    database_url: str
    allowed_origins: list[str]
    enable_failure_simulation: bool
    default_model: str
    default_system_prompt: str
    request_timeout_seconds: float
    cache_ttl_seconds: float
    keys: list[KeySlot] = field(default_factory=list)

    @property
    def database_path(self) -> str:
        url = self.database_url
        for prefix in ("sqlite:///", "sqlite://"):
            if url.startswith(prefix):
                return url[len(prefix):] or "app.db"
        return url


def load_settings() -> Settings:
    default_model = os.getenv("LLM_MODEL", "gemini/gemini-flash-latest").strip()
    api_keys = _list("LLM_API_KEYS")
    # Optional: give each key its own model (e.g. a second provider), aligned by position.
    key_models = _list("LLM_KEY_MODELS")
    keys = [
        KeySlot(
            key_id=f"key_{i + 1}",
            api_key=key,
            model=key_models[i] if i < len(key_models) else default_model,
        )
        for i, key in enumerate(api_keys)
    ]
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/app.db"),
        allowed_origins=_list("ALLOWED_ORIGINS", "http://localhost:3000"),
        enable_failure_simulation=_bool("ENABLE_FAILURE_SIMULATION"),
        default_model=default_model,
        default_system_prompt=os.getenv(
            "DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant."
        ),
        request_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        cache_ttl_seconds=float(os.getenv("CACHE_TTL_SECONDS", "86400")),
        keys=keys,
    )
