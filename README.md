# Lyftr AI – LLM Chatbot with Key Failover and Response Caching

React (Vite) frontend · FastAPI backend · LiteLLM gateway · SQLite storage · Docker.

```
browser ──► frontend (React, nginx :3000)
                │  POST /chat   (+ optional X-Simulate-Failure)
                ▼
            backend (FastAPI :8000)
              ├─ llm.py  key pool key_1..key_n → failover loop → LiteLLM acompletion
              │          LiteLLM in-memory cache (key = model + full message list)
              ├─ db.py   SQLite at DATABASE_URL (/data/app.db on a Docker volume)
              └─ main.py /health · /chat · /sessions/{id}/messages
```

## 1. Live URLs

| Tier | URL |
|---|---|
| Frontend (Vercel) | https://lyftr-ai-chatbot-theta.vercel.app |
| Backend (Render) | https://lyftr-chat-backend-72k0.onrender.com ([`/health`](https://lyftr-chat-backend-72k0.onrender.com/health)) |
| Repository | https://github.com/aryannnnnzzz/lyftr-ai-chatbot |

The backend runs on Render's free plan, which spins down when idle. The first request after a
pause can take about 50 seconds while it wakes up; later requests are fast. The free plan also
has no persistent disk, so the deployed SQLite history resets on redeploy. Locally,
`docker-compose` keeps it on the `appdata` volume.

## 2. Run locally

### With Docker

```bash
cp .env.example .env        # then put two real keys in LLM_API_KEYS
make up                     # builds both images and starts the stack
# frontend: http://localhost:3000   backend: http://localhost:8000/health
make down                   # stop (the SQLite volume is kept)
```

`make up` creates `.env` from `.env.example` if it doesn't exist. The SQLite file lives on the named
volume `appdata` mounted at `/data`, so history survives `make down && make up`. Use `make clean`
to delete the volume too.

### Without Docker

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key-one,key-two ENABLE_FAILURE_SIMULATION=true DATABASE_URL=sqlite:///./data/app.db
uvicorn app.main:app --port 8000
pytest -q                                          # 22 tests, no real keys needed

# frontend (another terminal)
cd frontend
npm install
npm run dev                                        # http://localhost:3000 → http://localhost:8000
```

### Environment variables

| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool (≥ 2). Ids are `key_1`, `key_2`, … in this order |
| `LLM_MODEL` | LiteLLM model for every key. The deployment uses `groq/openai/gpt-oss-20b`; any LiteLLM model works, e.g. `gemini/gemini-flash-latest` (the code default) |
| `LLM_KEY_MODELS` | Optional per-key models aligned with `LLM_API_KEYS` (e.g. mix Gemini + Groq) |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` header |
| `DEFAULT_SYSTEM_PROMPT` | Used when a session has no system prompt override |
| `LLM_TIMEOUT_SECONDS` | Per-key provider timeout (default 30) |
| `CACHE_TTL_SECONDS` | Cache entry lifetime (default 86400) |
| `API_BASE_URL` / `FRONTEND_API_BASE_URL` | Frontend container: backend URL written to `config.js` at start-up |
| `VITE_API_BASE_URL` | Frontend build-time fallback (used on Vercel) |

## 3. Design decisions

### Key fallback

- `LLM_API_KEYS` becomes an ordered pool of `KeySlot(key_id, api_key, model)`. Only the `key_id`
  ever leaves the server (responses, logs, history).
- For each request, `LLMGateway.complete()` walks the pool in order and calls
  `litellm.acompletion(model, messages, api_key=..., num_retries=0, timeout=...)`. Any provider
  failure, such as `RateLimitError` (429 and quota), 5xx (`InternalServerError`, `BadGatewayError`,
  `ServiceUnavailableError`), a timeout, a connection error or a revoked key, moves on to the next
  key. LiteLLM normalises every provider's errors into these typed exceptions.
  `fallback_attempts` counts the keys that failed first.
- `num_retries=0` is deliberate. Retrying a rate-limited key wastes the user's time when a healthy
  key is next in line.
- If every key fails, the request gets `503 {"detail": "all providers unavailable"}`. The
  exchange is only written to SQLite **after** a successful reply, in one transaction, so a failed
  request (422 or 503) never appears in history.
- **`X-Simulate-Failure`** (only honoured when `ENABLE_FAILURE_SIMULATION=true`) is parsed into
  `{key_id: status}`, with `*` matching every key and a specific key taking precedence. A targeted
  key raises *the same LiteLLM exception class* a real provider error would (`429→RateLimitError`,
  `500→InternalServerError`, `502→BadGatewayError`, `503→ServiceUnavailableError`) instead of
  calling the provider. Real and simulated failures therefore go through the same code path, and
  the request that falls over to the next key gets a genuine answer.
- I wrote the loop myself rather than using `litellm.Router`. I need the exact id of the key that
  answered and the failure count for the response contract, and a per-request failure plan. A
  short loop over `litellm.acompletion` gives both directly and is easier to verify.

### Caching

- LiteLLM's built-in cache (`litellm.caching.Cache(type="local")`, in-memory with a TTL). This
  LiteLLM version no longer ships the disk cache.
- The cache key is LiteLLM's own `get_cache_key()` over `model` + the full `messages` list
  (system prompt + history + new message). `api_key` is **not** part of it, so a reply cached via
  `key_1` is still served when `key_1` is down.
- The lookup happens just before the live call, at the first key that isn't (simulated) failing.
  - A hit returns the stored reply verbatim, with `resolution_source=LLM_CACHE`, the stored token
    usage, `key_id=null` and `fallback_attempts=0`.
  - A miss makes the live call, then writes the response to the cache with
    `async_add_cache`, and the reply is labelled `LIVE_INFERENCE`.
  - The cache is read and written explicitly instead of relying on `caching=True` side effects,
    so the label is always correct and the path is testable.
- Consequences:
  - The same first message in a new session is a hit.
  - A different message, or the same text after different history, is never a hit.
  - `*=429` returns 503 even for a cached prompt, because the failure plan is applied first, as
    the contract table requires.

### SQLite session storage

- Stdlib `sqlite3`, WAL mode, one short-lived connection per operation. The schema is created on
  start-up.
- `messages` matches the required model exactly, plus an index on `(session_id, id)`.
- An extra `sessions(session_id, system_prompt, created_at)` table remembers each session's system
  prompt override and lets `GET /sessions/{id}/messages` return a fast 404 for unknown ids.
- The client sends only the new message. The server rebuilds
  `[system] + history + [user]` from SQLite on every turn.
- `DATABASE_URL=sqlite:////data/app.db` points at the Docker named volume, so history survives
  container restarts.

### Errors and validation

- Pydantic validates the request:
  - `session_id` must be 1–128 characters and not blank.
  - `message` is trimmed, then must be 1–4000 characters.
  - A blank `system_prompt` counts as absent.
- Bad bodies and malformed JSON get FastAPI's 422 JSON.
- A catch-all handler returns `{"detail": "internal server error"}`, so a stack trace or HTML
  error page never reaches the client.
- `latency_ms` is measured with `perf_counter` around the whole handler.

## 4. Verification

Set `B=http://localhost:8000` (or the live backend URL).

```bash
curl $B/health
# {"status":"ok","keys_configured":2,"cache_enabled":true}

# Live call, then a cache hit from a NEW session with the same first message
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-1","message":"What is the capital of France?"}'
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-2","message":"What is the capital of France?"}'
# → second has "resolution_source":"LLM_CACHE", identical reply, key_id null, much lower latency_ms

# Failover: key_1 fails, key_2 answers
curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"demo-1","message":"And of Germany?"}'
# → 200, "key_id":"key_2", "fallback_attempts":1 (and it remembers France from turn 1)

# Every key fails
curl -s -i -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"demo-1","message":"hello?"}'
# → 503 {"detail":"all providers unavailable"}, and nothing is added to history

curl -s $B/sessions/demo-1/messages      # history, oldest first, no system prompt
curl -s -i $B/sessions/nope/messages     # 404 {"detail":"session not found"}
curl -s -i -X POST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"x","message":"  "}'  # 422
```

**In the UI:**
- Each assistant reply shows chips for source (`LIVE_INFERENCE` in green, `LLM_CACHE` in
  orange), model, latency, the **input and output tokens for that request only**, and the key that
  answered with its failover count.
- The *Failover demo* dropdown sends `X-Simulate-Failure` (e.g. `key_1 → 429`), so failover and
  the 503 path can be shown without curl.
- Reloading the page or clicking *New chat* starts a new session. Sending the same first message
  again then shows `LLM_CACHE`.

**Automated tests:** `cd backend && pytest -q` runs 22 tests with the provider mocked at
`litellm.acompletion`. They cover:
- the health check and the response contract
- a cache hit or miss, identical replies, and a hit being faster than a live call
- per-request tokens
- multi-turn history and separate sessions
- system prompt persistence
- 404 for an unknown session
- simulated 429 and 500 failover, `*=429` returning 503 with no history written
- failover on a real `RateLimitError`
- the simulation switch
- every 422 case, including malformed JSON
- the header parser

## 5. Setup Used

- Editor: Claude desktop app (Claude Code tab) with the built-in terminal
- AI tools: Claude Code (Claude Opus) for scaffolding, implementation, tests and docs; all code reviewed and run locally.

## 6. Bonus features

- **Failover demo control in the UI:** a dropdown that sets `X-Simulate-Failure`, so reviewers can
  trigger failover and the 503 path from the browser.
- **Per-reply key and failover chip:** shows which `key_id` answered and how many keys failed first.
- **Per-key models (`LLM_KEY_MODELS`):** the pool can span providers (e.g. Gemini as primary and
  Groq as backup). An outage at one provider then doesn't take the app down.
- **Session system prompt:** the optional field is stored per session in SQLite and applied to
  every later turn.
- **Runtime frontend config:** the nginx image writes `config.js` from `API_BASE_URL` at start-up,
  so one image works for any backend URL without a rebuild.
- **Health check that tells the truth:** `/health` returns 503 with a reason when the database is
  unreachable or no keys are configured. Both Dockerfiles use small multi-stage images, and the
  backend runs as a non-root user with a Docker `HEALTHCHECK`.

## Deployment notes

- **Backend (Render):**
  1. New → Web Service → this repo, with Root Directory `backend` and the Docker runtime. Or use
     `render.yaml` as a Blueprint.
  2. Set `LLM_API_KEYS`, `ALLOWED_ORIGINS=https://<frontend>.vercel.app` and
     `ENABLE_FAILURE_SIMULATION=true`.
  3. Render's free tier has an ephemeral filesystem. Attach a disk at `/data`, or use Railway or
     Fly with a volume, if history must survive a redeploy. The image runs as uid 10001; on
     Railway volumes set `RAILWAY_RUN_UID=0` so the mounted `/data` is writable.
- **Frontend (Vercel):** import the repo with Root Directory `frontend` and the Vite preset, and
  set `VITE_API_BASE_URL=https://<backend>.onrender.com`.
