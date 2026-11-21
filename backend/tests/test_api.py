import litellm


def chat(client, session_id, message, headers=None, **extra):
    return client.post(
        "/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {}
    )


# ---------- health ----------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


# ---------- happy path + contract ----------

def test_chat_live_inference_contract(client, provider):
    r = chat(client, "s-1", "  What is the capital of France?  ")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "s-1"
    assert body["reply"]
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/test-model"
    assert isinstance(meta["latency_ms"], int)
    assert meta["input_tokens"] > 0 and meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0
    # message is trimmed before being sent; key itself is never returned
    assert provider.calls[0]["messages"][-1]["content"] == "What is the capital of France?"
    assert "secret" not in r.text


# ---------- caching ----------

def test_same_first_message_in_new_session_hits_cache(client, provider):
    first = chat(client, "s-a", "Hello cache").json()
    second = chat(client, "s-b", "Hello cache").json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    assert second["metadata"]["latency_ms"] < first["metadata"]["latency_ms"]
    assert len(provider.calls) == 1


def test_different_message_never_from_cache(client):
    chat(client, "s-a", "Message one")
    r = chat(client, "s-b", "Message two").json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_same_message_different_history_is_not_cached(client):
    chat(client, "s-a", "Hi")
    r = chat(client, "s-a", "Hi").json()  # same text, but history differs now
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# ---------- tokens ----------

def test_tokens_are_per_request_not_cumulative(client):
    a = chat(client, "s-a", "alpha beta gamma").json()["metadata"]["input_tokens"]
    b = chat(client, "s-b", "delta epsilon zeta").json()["metadata"]["input_tokens"]
    assert a == b


# ---------- multi-turn ----------

def test_multi_turn_history_and_isolation(client, provider):
    chat(client, "s-a", "My name is Ada")
    chat(client, "s-a", "What is my name?")
    sent = provider.calls[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "My name is Ada"

    chat(client, "s-b", "Who am I?")
    assert [m["role"] for m in provider.calls[-1]["messages"]] == ["system", "user"]

    hist = client.get("/sessions/s-a/messages").json()
    assert hist["session_id"] == "s-a"
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant", "user", "assistant"]
    assert hist["messages"][0] == {"role": "user", "content": "My name is Ada"}
    meta = hist["messages"][1]["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert "input_tokens" in meta and "output_tokens" in meta


def test_system_prompt_override_persists_for_session(client, provider):
    chat(client, "s-p", "hi", system_prompt="Talk like a pirate.")
    chat(client, "s-p", "again")
    assert provider.calls[-1]["messages"][0] == {"role": "system", "content": "Talk like a pirate."}
    hist = client.get("/sessions/s-p/messages").json()["messages"]
    assert all(m["role"] != "system" for m in hist)


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


# ---------- failover ----------

def test_simulated_429_fails_over_to_key_2(client, provider):
    r = chat(client, "s-f", "failover please", headers={"X-Simulate-Failure": "key_1=429"})
    assert r.status_code == 200
    meta = r.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert provider.calls[-1]["api_key"] == "secret-two"
    assert r.json()["reply"].startswith("echo:")


def test_simulated_500_fails_over_to_key_2(client):
    r = chat(client, "s-f", "five hundred", headers={"X-Simulate-Failure": "key_1=500"})
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_fail_returns_503_and_no_history(client, provider):
    r = chat(client, "s-x", "doomed", headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert provider.calls == []
    assert client.get("/sessions/s-x/messages").status_code == 404


def test_failed_turn_not_added_to_existing_session(client):
    chat(client, "s-y", "first")
    chat(client, "s-y", "second", headers={"X-Simulate-Failure": "key_1=503,key_2=502"})
    assert len(client.get("/sessions/s-y/messages").json()["messages"]) == 2


def test_real_provider_rate_limit_fails_over(client, provider):
    provider.failing_keys["secret-one"] = litellm.RateLimitError(
        message="quota exceeded", llm_provider="gemini", model="test-model"
    )
    r = chat(client, "s-r", "real failure")
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"
    assert r.json()["metadata"]["fallback_attempts"] == 1


def test_simulation_ignored_when_disabled(client):
    client.app.state.gateway.simulation_enabled = False
    r = chat(client, "s-d", "no sim", headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_1"


# ---------- validation ----------

def test_validation_errors_are_422_json(client):
    cases = [
        {"session_id": "s", "message": ""},
        {"session_id": "s", "message": "   "},
        {"session_id": "", "message": "hi"},
        {"session_id": "x" * 129, "message": "hi"},
        {"session_id": "s", "message": "a" * 4001},
        {"message": "hi"},
        {"session_id": "s"},
    ]
    for payload in cases:
        r = client.post("/chat", json=payload)
        assert r.status_code == 422, payload
        assert "detail" in r.json()


def test_message_of_exactly_4000_chars_after_trim_ok(client):
    r = chat(client, "s-long", "  " + "a" * 4000 + "  ")
    assert r.status_code == 200


def test_malformed_json_is_422(client):
    r = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "detail" in r.json()
