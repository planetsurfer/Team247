// Try-your-agent chat — Iteration 2 (user-value loop). A scoped live chat
// against this specific delivered agent's composed skill bundle
// (POST /api/team/{tid}/agents/{aid}/chat), embedded below the DeliverCard's
// action buttons. Collapsed by default so it doesn't crowd the delivered-
// agent summary; expands on click. Suggested first-message chips are derived
// client-side from the task text — no server round trip needed just to show
// them, and they vanish once the thread has any messages.
import { useEffect, useRef, useState } from "react";
import { C } from "../../theme";
import type { ChatThreadState } from "../../types";

interface TryAgentChatProps {
  roleName: string;
  useCase?: string;
  accent: string;
  chat?: ChatThreadState;
  onSend: (text: string) => void;
  // Iteration 4 (starter gallery) — when provided, the caller owns the
  // collapsed/expanded state (e.g. the gallery detail panel's own "Try this
  // agent" action button) and this component's internal toggle button is
  // suppressed. Omit for the original DeliverCard usage, which keeps its own
  // internal collapsed-by-default "▸ Try your agent" toggle unchanged.
  expanded?: boolean;
}

function suggestedChips(useCase?: string): string[] {
  const trimmed = (useCase ?? "").trim();
  const situation = trimmed
    ? `Here's my situation: ${trimmed}`
    : "Here's my situation: ";
  return [
    situation,
    "What do you need from me to start?",
    "Walk me through your process for this task",
  ];
}

export function TryAgentChat({
  roleName, useCase, accent, chat, onSend, expanded,
}: TryAgentChatProps) {
  const controlled = expanded !== undefined;
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlled ? !!expanded : internalOpen;
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const messages = chat?.messages ?? [];
  const busy = !!chat?.busy;

  // Auto-scroll the (capped-height) message list to the newest turn — this
  // box scrolls independently of the outer Thread, which only auto-scrolls
  // its own list on new *messages* (kind-tagged cards), not on state changes
  // inside a single deliver card's chat sub-thread.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, busy]);

  const send = (text: string) => {
    const clean = text.trim();
    if (!clean || busy) return;
    onSend(clean);
    setDraft("");
  };

  return (
    <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${C.divider}` }}>
      {!controlled && (
        <button
          onClick={() => setInternalOpen((v) => !v)}
          style={{
            border: "none",
            background: "transparent",
            padding: 0,
            fontSize: 12.5,
            fontWeight: 600,
            color: C.muted,
            cursor: "pointer",
          }}
        >
          {open ? "▾" : "▸"} Try your agent
        </button>
      )}
      {open && (
        <div style={{ marginTop: 10 }}>
          {messages.length === 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
              {suggestedChips(useCase).map((chip, i) => (
                <button
                  key={i}
                  onClick={() => send(chip)}
                  style={{
                    border: `1px solid ${C.pillBorder}`,
                    background: C.chipBg,
                    color: "#3c3a33",
                    borderRadius: 14,
                    padding: "5px 10px",
                    fontSize: 11.5,
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  {chip}
                </button>
              ))}
            </div>
          )}
          {messages.length > 0 && (
            <div
              ref={listRef}
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 8,
                marginBottom: 10,
                maxHeight: 260,
                overflowY: "auto",
              }}
            >
              {messages.map((m, i) => (
                <div
                  key={i}
                  style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}
                >
                  <div
                    style={{
                      background: m.role === "user" ? C.userBubble : "#fff",
                      border: m.role === "user" ? "none" : `1px solid ${C.pillBorder}`,
                      borderRadius: m.role === "user" ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
                      padding: "8px 12px",
                      maxWidth: "85%",
                      fontSize: 13,
                      lineHeight: 1.5,
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {m.content}
                  </div>
                </div>
              ))}
              {busy && <div style={{ fontSize: 12, color: C.dim }}>{roleName || "Your agent"} is typing…</div>}
            </div>
          )}
          {chat?.error && (
            <div style={{ fontSize: 12, color: C.gap, marginBottom: 8 }}>{chat.error}</div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") send(draft);
              }}
              placeholder={`Message ${roleName || "your agent"}…`}
              disabled={busy}
              style={{
                flex: 1,
                border: `1px solid ${C.pillBorder}`,
                borderRadius: 10,
                padding: "8px 12px",
                fontSize: 13,
                color: C.ink,
                background: "#fff",
              }}
            />
            <button
              onClick={() => send(draft)}
              disabled={busy || !draft.trim()}
              style={{
                border: "none",
                background: busy || !draft.trim() ? C.disabledSend : accent,
                color: "#fff",
                borderRadius: 10,
                padding: "8px 14px",
                fontSize: 13,
                fontWeight: 600,
                cursor: busy || !draft.trim() ? "default" : "pointer",
              }}
            >
              Send
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
