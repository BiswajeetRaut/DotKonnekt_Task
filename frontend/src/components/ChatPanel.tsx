import { FormEvent, useEffect, useRef, useState } from "react";
import { ChatMessage, api } from "../api";

export function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getChatHistory().then(setMessages).catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setError(null);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setSending(true);
    try {
      const { reply } = await api.sendChatMessage(text);
      setMessages((prev) => [...prev, { role: "assistant", content: reply }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="card">
      <h2>Ask about your spending</h2>
      <div className="chat-log">
        {messages.length === 0 && (
          <p className="chat-empty">
            Try: "How much did I spend on food last month?" or "Why was my last expense flagged?"
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-message chat-message--${m.role}`}>
            {m.content}
          </div>
        ))}
        {sending && <div className="chat-message chat-message--assistant chat-message--pending">Thinking…</div>}
        <div ref={bottomRef} />
      </div>
      <form className="inline-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question about your expenses…"
          disabled={sending}
        />
        <button type="submit" disabled={sending || !input.trim()}>
          Send
        </button>
      </form>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
