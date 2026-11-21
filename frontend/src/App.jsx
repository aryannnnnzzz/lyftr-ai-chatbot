import { useEffect, useRef, useState } from "react";
import { API_BASE_URL, newSessionId, sendChat } from "./api.js";

const SIMULATIONS = [
  { value: "", label: "No failure simulation" },
  { value: "key_1=429", label: "key_1 → 429 (rate limit)" },
  { value: "key_1=500", label: "key_1 → 500 (server error)" },
  { value: "key_1=429,key_2=500", label: "key_1 → 429, key_2 → 500" },
  { value: "*=429", label: "All keys → 429" },
];

function AssistantMessage({ msg }) {
  const m = msg.metadata;
  const cached = m.resolution_source === "LLM_CACHE";
  return (
    <div className="msg assistant" data-testid="assistant-message">
      <div className="bubble">{msg.content}</div>
      <div className="meta">
        <span className={`chip ${cached ? "chip-cache" : "chip-live"}`} data-testid="meta-source">
          {m.resolution_source}
        </span>
        <span className="chip" title="Model">
          <span data-testid="meta-model">{m.model}</span>
        </span>
        <span className="chip" title="Latency">
          <span data-testid="meta-latency">{m.latency_ms} ms</span>
        </span>
        <span className="chip" title="Input / output tokens for this request">
          in <span data-testid="meta-input-tokens">{m.input_tokens}</span>
          {" · "}out <span data-testid="meta-output-tokens">{m.output_tokens}</span>
        </span>
        <span className="chip chip-subtle" title="Key that answered / keys that failed first">
          {m.key_id ?? "no key (cache)"}
          {m.fallback_attempts > 0 ? ` · ${m.fallback_attempts} failover` : ""}
        </span>
      </div>
    </div>
  );
}

export default function App() {
  const [sessionId] = useState(newSessionId); // new session on every page load
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulateFailure, setSimulateFailure] = useState("");
  const [pending, setPending] = useState(false);
  const listRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pending]);

  async function send() {
    const text = input.trim();
    if (!text || pending) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setPending(true);
    try {
      const data = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.reply, metadata: data.metadata },
      ]);
    } catch (err) {
      const prefix = err.status ? `Error ${err.status}: ` : "Error: ";
      setMessages((prev) => [...prev, { role: "error", content: prefix + err.message }]);
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true">◆</span>
          <div>
            <h1>Lyftr Chat</h1>
            <p className="sub">
              Session <code title={sessionId}>{sessionId.slice(0, 14)}…</code>
              {" · "}
              <a
                href={`${API_BASE_URL}/sessions/${encodeURIComponent(sessionId)}/messages`}
                target="_blank"
                rel="noreferrer"
              >
                View history (JSON)
              </a>
            </p>
          </div>
        </div>
        <button className="ghost" onClick={() => window.location.reload()}>
          New chat
        </button>
      </header>

      <section className="controls">
        <label className="field">
          <span>System prompt (optional)</span>
          <input
            data-testid="system-prompt-input"
            type="text"
            placeholder="You are a helpful, concise assistant."
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
          />
        </label>
        <label className="field narrow">
          <span>Failover demo (X-Simulate-Failure)</span>
          <select value={simulateFailure} onChange={(e) => setSimulateFailure(e.target.value)}>
            {SIMULATIONS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </section>

      <main className="messages" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty">
            <h2>Start a conversation</h2>
            <p>
              Each reply shows where it came from (<b>LIVE_INFERENCE</b> or <b>LLM_CACHE</b>), the
              model, latency and the tokens used by that request only.
            </p>
          </div>
        )}
        {messages.map((msg, i) =>
          msg.role === "assistant" ? (
            <AssistantMessage key={i} msg={msg} />
          ) : msg.role === "user" ? (
            <div key={i} className="msg user">
              <div className="bubble">{msg.content}</div>
            </div>
          ) : (
            <div key={i} className="msg error" role="alert">
              <div className="bubble">{msg.content}</div>
            </div>
          )
        )}
        {pending && (
          <div className="msg assistant">
            <div className="bubble typing">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
      </main>

      <footer className="composer">
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          rows={1}
          maxLength={4000}
          placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus
        />
        <button
          data-testid="send-button"
          className="primary"
          onClick={send}
          disabled={pending || !input.trim()}
        >
          {pending ? "Sending…" : "Send"}
        </button>
      </footer>
      <p className="api-note">
        Backend: <code>{API_BASE_URL}</code>
      </p>
    </div>
  );
}
