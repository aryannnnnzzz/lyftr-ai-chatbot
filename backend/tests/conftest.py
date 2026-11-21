import asyncio
from typing import Any

import litellm
import pytest
from fastapi.testclient import TestClient

from app import llm as llm_module
from app.config import KeySlot, Settings
from app.main import create_app


class FakeProvider:
    """Stands in for litellm.acompletion. Records calls and can make real keys fail."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.failing_keys: dict[str, Exception] = {}
        self.delay = 0.05  # make live calls measurably slower than cache hits

    async def __call__(self, *, model: str, messages: list[dict], api_key: str, **_: Any):
        self.calls.append({"model": model, "messages": messages, "api_key": api_key})
        if api_key in self.failing_keys:
            raise self.failing_keys[api_key]
        await asyncio.sleep(self.delay)
        last = messages[-1]["content"]
        prompt_tokens = sum(len(m["content"].split()) + 4 for m in messages)
        reply = f"echo: {last} (history={len(messages) - 2})"
        return litellm.ModelResponse(
            model=model.split("/")[-1],
            choices=[{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": reply}}],
            usage={"prompt_tokens": prompt_tokens, "completion_tokens": len(reply.split()),
                   "total_tokens": prompt_tokens + len(reply.split())},
        )


@pytest.fixture
def provider(monkeypatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)
    return fake


@pytest.fixture
def client(tmp_path, provider) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        allowed_origins=["http://localhost:3000"],
        enable_failure_simulation=True,
        default_model="gemini/test-model",
        default_system_prompt="You are a test bot.",
        request_timeout_seconds=5,
        cache_ttl_seconds=60,
        keys=[
            KeySlot("key_1", "secret-one", "gemini/test-model"),
            KeySlot("key_2", "secret-two", "gemini/test-model"),
        ],
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
