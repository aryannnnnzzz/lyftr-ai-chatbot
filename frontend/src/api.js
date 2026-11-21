const runtime = (typeof window !== "undefined" && window.__APP_CONFIG__) || {};

export const API_BASE_URL = (
  runtime.API_BASE_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "http://localhost:8000"
).replace(/\/+$/, "");

export function newSessionId() {
  const id =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `s-${id}`;
}

function describeError(status, body) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  }
  return `Request failed with status ${status}`;
}

/** POST /chat. Resolves to the JSON body or throws an Error with a readable message. */
export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;

  const payload = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();

  let res;
  try {
    res = await fetch(`${API_BASE_URL}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error(`Cannot reach the backend at ${API_BASE_URL}. Is it running?`);
  }

  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON body: handled below */
  }
  if (!res.ok) {
    const err = new Error(describeError(res.status, body));
    err.status = res.status;
    throw err;
  }
  return body;
}
