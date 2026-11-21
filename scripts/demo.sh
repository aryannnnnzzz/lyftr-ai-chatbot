#!/usr/bin/env bash
# Walks through every endpoint of the API contract against a running backend.
# Usage: ./scripts/demo.sh [BASE_URL]     (default: the deployed Render backend)
set -u
B="${1:-https://lyftr-chat-backend-72k0.onrender.com}"
H='Content-Type: application/json'
RUN=$RANDOM                      # unique text so the first call is always a cache miss
A="demo-a-$RUN"; Bs="demo-b-$RUN"

step() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
post() { curl -s -w '   [HTTP %{http_code}]\n' -X POST "$B/chat" -H "$H" "$@"; }

step "1. GET /health"
curl -s -w '   [HTTP %{http_code}]\n' "$B/health"

step "2. POST /chat  - new session, live call"
post -d "{\"session_id\":\"$A\",\"message\":\"What is the capital of France? (run $RUN)\"}"

step "3. Same first message, NEW session  -> LLM_CACHE, same reply, key_id null, ~1 ms"
post -d "{\"session_id\":\"$Bs\",\"message\":\"What is the capital of France? (run $RUN)\"}"

step "4. Failover: X-Simulate-Failure: key_1=429  -> answered by key_2 (and remembers turn 1)"
post -H 'X-Simulate-Failure: key_1=429' \
     -d "{\"session_id\":\"$A\",\"message\":\"Which country did I just ask about? One word.\"}"

step "5. Failover: key_1=500  -> key_2"
post -H 'X-Simulate-Failure: key_1=500' \
     -d "{\"session_id\":\"$A\",\"message\":\"And its most famous tower? One short line.\"}"

step "6. Every key fails: *=429  -> 503, nothing stored"
post -H 'X-Simulate-Failure: *=429' -d "{\"session_id\":\"$A\",\"message\":\"this should fail\"}"

step "7. GET /sessions/$A/messages  -> 6 messages, oldest first, no system prompt, no failed turn"
curl -s -w '   [HTTP %{http_code}]\n' "$B/sessions/$A/messages"

step "8. Unknown session -> 404"
curl -s -w '   [HTTP %{http_code}]\n' "$B/sessions/does-not-exist-$RUN/messages"

step "9. Validation -> 422 (empty message, then malformed JSON)"
post -d '{"session_id":"x","message":"   "}'
post -d '{not json'
