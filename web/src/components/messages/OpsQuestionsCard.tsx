// Ops-question interview card — Iteration 2 ("make it yours" UI). Renders
// right below the TeamCard once a fire-and-forget POST /ops-questions comes
// back with 1..OPS_QUESTIONS_MAX questions (0 renders nothing — see
// useChat's loadOpsQuestions). Each question is a single-line answer; Save
// PUTs the non-empty answers (merged with whatever's already saved for the
// team) back to /api/team/{team_id}/inputs, so they're baked into the
// generated SKILL.md's "### Your provided inputs" section on the next
// render/verify/export — no extra wiring needed (bundle cache keys off
// teams.user_inputs).
import { useState } from "react";
import { C } from "../../theme";
import type { OpsQuestionsState, TranscriptState } from "../../types";

// Client-side cap only (UX guardrail — counter + disables Extract over the
// limit). The server enforces the real 200KB UTF-8-byte cap
// (app/routers/team.py TRANSCRIPT_MAX_BYTES) regardless of this; JS string
// .length is UTF-16 code units, not bytes, so this is an approximation.
const TRANSCRIPT_CLIENT_MAX_CHARS = 200_000;

interface OpsQuestionsCardProps {
  ops: OpsQuestionsState;
  accent: string;
  onAnswer: (idx: number, text: string) => void;
  onSave: () => void;
  onDismiss: () => void;
  // Iteration 3 (OPERATIONS-INTAKE loop) — "paste a meeting transcript
  // instead" panel. Defaults to false/omitted so nothing renders unless a
  // caller explicitly opts in.
  enableTranscript?: boolean;
  transcript?: TranscriptState;
  onTranscriptToggle?: () => void;
  onTranscriptTextChange?: (text: string) => void;
  onTranscriptExtract?: () => void;
  onTranscriptItemEdit?: (idx: number, text: string) => void;
  onTranscriptItemRemove?: (idx: number) => void;
  onTranscriptConfirm?: () => void;
  onTranscriptCancel?: () => void;
}

const EYEBROW: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 650,
  letterSpacing: "1.2px",
  textTransform: "uppercase",
  color: C.dim,
};
const CARD: React.CSSProperties = {
  background: C.cardBg,
  border: `1px solid ${C.cardBorder}`,
  borderRadius: 16,
  padding: "18px 20px",
  maxWidth: 640,
};

export function OpsQuestionsCard({
  ops,
  accent,
  onAnswer,
  onSave,
  onDismiss,
  enableTranscript,
  transcript,
  onTranscriptToggle,
  onTranscriptTextChange,
  onTranscriptExtract,
  onTranscriptItemEdit,
  onTranscriptItemRemove,
  onTranscriptConfirm,
  onTranscriptCancel,
}: OpsQuestionsCardProps) {
  const [hovered, setHovered] = useState(false);
  // Iteration 4 (convergence + polish): an empty question list still renders
  // when `done` — the converged "nothing more to ask" state — so the user
  // sees the loop close instead of the card just silently vanishing. The
  // ordinary empty-on-first-load case (no questions were ever asked at all)
  // never sets `done` — see useChat's loadOpsQuestions — so it still renders
  // nothing, unchanged from Iteration 2.
  if (ops.dismissed || (ops.questions.length === 0 && !ops.done)) return null;

  const hasAnswer = ops.questions.some((_, i) => (ops.answers[i] ?? "").trim().length > 0);
  const busy = ops.busy;

  return (
    <div style={CARD}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div style={EYEBROW}>Make these agents yours</div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss — skip these questions"
          style={{
            border: "none",
            background: "transparent",
            color: C.faint,
            fontSize: 13,
            padding: "0 0 0 8px",
            cursor: "pointer",
            lineHeight: 1,
          }}
        >
          ✕
        </button>
      </div>
      {ops.done ? (
        // Iteration 4 (convergence + polish) — done state: replaces the
        // question list + Save row entirely (nothing left to answer), so the
        // card collapses down to a single muted line instead of holding its
        // full height. Still dismissible via the ✕ above; the transcript
        // entry point below stays available in case something new comes up.
        <div style={{ marginTop: 6, fontSize: 12, color: C.dim }}>
          Nothing more to ask — your agents have what they need ✓
        </div>
      ) : (
        <>
          <div style={{ marginTop: 6, fontSize: 12.5, color: C.muted }}>
            Answer a couple of quick questions so your agents follow YOUR rules — or skip.
          </div>
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 12 }}>
            {ops.questions.map((q, i) => (
              <div key={i}>
                <label
                  htmlFor={`ops-q-${i}`}
                  style={{ display: "block", fontSize: 12.5, fontWeight: 600, color: "#3c3a33" }}
                >
                  {q.question}
                </label>
                <input
                  id={`ops-q-${i}`}
                  value={ops.answers[i] ?? ""}
                  onChange={(e) => onAnswer(i, e.target.value)}
                  placeholder={q.name}
                  disabled={busy}
                  style={{
                    width: "100%",
                    marginTop: 5,
                    boxSizing: "border-box",
                    fontSize: 12.5,
                    fontFamily: "inherit",
                    padding: "7px 9px",
                    borderRadius: 8,
                    border: `1px solid ${C.divider}`,
                    color: C.ink,
                    background: "#fff",
                  }}
                />
              </div>
            ))}
          </div>
          <div
            style={{
              marginTop: 12,
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexWrap: "wrap",
            }}
          >
            <button
              type="button"
              onClick={onSave}
              disabled={!hasAnswer || busy}
              style={{
                border: "none",
                background: hasAnswer && !busy ? accent : C.disabledSend,
                color: "#fff",
                borderRadius: 8,
                padding: "7px 14px",
                fontSize: 12.5,
                fontWeight: 600,
                cursor: !hasAnswer || busy ? "not-allowed" : "pointer",
              }}
            >
              {busy ? "Saving…" : "Save answers"}
            </button>
            {ops.saved && !busy && (
              <span style={{ fontSize: 11.5, color: C.success }}>
                ✓ {ops.savedCount ?? 0} answer{(ops.savedCount ?? 0) === 1 ? "" : "s"} saved —
                they'll be baked into your agents
              </span>
            )}
            {ops.error && !busy && (
              <span style={{ fontSize: 11.5, color: C.gap }}>{ops.error}</span>
            )}
          </div>
          {ops.saved && !busy && (
            <div style={{ marginTop: 4, fontSize: 11, color: C.dim }}>
              Generating now includes your answers.
            </div>
          )}
        </>
      )}
      {enableTranscript && (
        <div style={{ marginTop: 10 }}>
          {!transcript?.open && (
            <button
              type="button"
              onClick={onTranscriptToggle}
              onMouseEnter={() => setHovered(true)}
              onMouseLeave={() => setHovered(false)}
              style={{
                border: "none",
                background: "transparent",
                padding: 0,
                fontSize: 11.5,
                color: C.faint,
                textDecoration: hovered ? "underline" : "none",
                cursor: "pointer",
              }}
            >
              Or paste a meeting transcript instead
            </button>
          )}
          {transcript?.open && (
            <TranscriptPanel
              accent={accent}
              transcript={transcript}
              onTextChange={onTranscriptTextChange}
              onExtract={onTranscriptExtract}
              onItemEdit={onTranscriptItemEdit}
              onItemRemove={onTranscriptItemRemove}
              onConfirm={onTranscriptConfirm}
              onCancel={onTranscriptCancel}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ── transcript panel (Iteration 3 — OPERATIONS-INTAKE loop) ────────────────
// Two states: (1) no proposal yet — textarea + Extract; (2) a proposal came
// back — an EDITABLE confirmation list (kind chip + name + editable content +
// remove ✕ per item) plus a separate "Your team should decide:" preview of
// any open_questions, then Confirm/Cancel.
interface TranscriptPanelProps {
  accent: string;
  transcript: TranscriptState;
  onTextChange?: (text: string) => void;
  onExtract?: () => void;
  onItemEdit?: (idx: number, text: string) => void;
  onItemRemove?: (idx: number) => void;
  onConfirm?: () => void;
  onCancel?: () => void;
}

const PANEL: React.CSSProperties = {
  marginTop: 10,
  padding: "12px 14px",
  background: "#f6f4ee",
  border: `1px solid ${C.divider}`,
  borderRadius: 10,
};

function TranscriptPanel({
  accent,
  transcript,
  onTextChange,
  onExtract,
  onItemEdit,
  onItemRemove,
  onConfirm,
  onCancel,
}: TranscriptPanelProps) {
  const { text, busy, error, proposal } = transcript;
  const overCap = text.length > TRANSCRIPT_CLIENT_MAX_CHARS;

  if (!proposal) {
    return (
      <div style={PANEL}>
        <div style={EYEBROW}>Paste a meeting transcript</div>
        <div style={{ marginTop: 4, fontSize: 11.5, color: C.muted }}>
          We'll pull out the operational rules, thresholds, and handoffs that are
          actually relevant — nothing else is kept, and the transcript itself is
          never saved.
        </div>
        <textarea
          value={text}
          onChange={(e) => onTextChange?.(e.target.value)}
          placeholder="Paste the transcript text here…"
          rows={8}
          disabled={busy}
          style={{
            width: "100%",
            marginTop: 8,
            boxSizing: "border-box",
            fontSize: 12.5,
            fontFamily: "inherit",
            padding: 8,
            borderRadius: 8,
            border: `1px solid ${C.divider}`,
            resize: "vertical",
          }}
        />
        <div
          style={{
            marginTop: 4,
            fontSize: 11,
            color: overCap ? C.gap : C.dim,
          }}
        >
          {text.length.toLocaleString()} / {TRANSCRIPT_CLIENT_MAX_CHARS.toLocaleString()} chars
        </div>
        <div
          style={{
            marginTop: 8,
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            onClick={onExtract}
            disabled={busy || !text.trim() || overCap}
            style={{
              border: "none",
              background: !busy && text.trim() && !overCap ? accent : C.disabledSend,
              color: "#fff",
              borderRadius: 8,
              padding: "7px 14px",
              fontSize: 12.5,
              fontWeight: 600,
              cursor: busy || !text.trim() || overCap ? "not-allowed" : "pointer",
            }}
          >
            {busy ? "Extracting…" : "Extract"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            style={{
              border: "none",
              background: "transparent",
              color: C.muted,
              fontSize: 12.5,
              fontWeight: 600,
              cursor: busy ? "not-allowed" : "pointer",
              padding: 0,
            }}
          >
            Cancel
          </button>
          <span style={{ fontSize: 11, color: C.dim }}>Uses one generation</span>
          {error && <span style={{ fontSize: 11.5, color: C.gap }}>{error}</span>}
        </div>
      </div>
    );
  }

  const isEmpty = proposal.items.length === 0 && proposal.open_questions.length === 0;

  return (
    <div style={PANEL}>
      <div style={EYEBROW}>Review before saving</div>
      {isEmpty && (
        <div style={{ marginTop: 6, fontSize: 12.5, color: C.muted }}>
          Nothing operationally relevant was found in that transcript.
        </div>
      )}
      {proposal.items.length > 0 && (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
          {proposal.items.map((it, i) => {
            const removed = !!transcript.removedItems[i];
            return (
              <div
                key={i}
                style={{
                  opacity: removed ? 0.45 : 1,
                  border: `1px solid ${C.divider}`,
                  borderRadius: 8,
                  padding: 8,
                  background: "#fff",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 650,
                        textTransform: "uppercase",
                        letterSpacing: ".4px",
                        color: accent,
                        background: "#fff",
                        border: `1px solid ${accent}`,
                        borderRadius: 999,
                        padding: "1px 7px",
                        flex: "none",
                      }}
                    >
                      {it.kind}
                    </span>
                    <span
                      style={{
                        fontSize: 12.5,
                        fontWeight: 600,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {it.name}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => onItemRemove?.(i)}
                    aria-label={removed ? `Restore ${it.name}` : `Remove ${it.name}`}
                    style={{
                      border: "none",
                      background: "transparent",
                      color: C.faint,
                      fontSize: 12,
                      cursor: "pointer",
                      lineHeight: 1,
                      flex: "none",
                    }}
                  >
                    {removed ? "↺ restore" : "✕"}
                  </button>
                </div>
                {!removed && (
                  <textarea
                    value={transcript.itemDrafts[i] ?? it.content}
                    onChange={(e) => onItemEdit?.(i, e.target.value)}
                    rows={2}
                    style={{
                      width: "100%",
                      marginTop: 6,
                      boxSizing: "border-box",
                      fontSize: 12,
                      fontFamily: "inherit",
                      padding: 6,
                      borderRadius: 6,
                      border: `1px solid ${C.divider}`,
                      resize: "vertical",
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
      {proposal.open_questions.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 650,
              letterSpacing: ".6px",
              textTransform: "uppercase",
              color: C.dim,
            }}
          >
            Your team should decide:
          </div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {proposal.open_questions.map((q, i) => (
              <li key={i} style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>
                {q.question}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div
        style={{
          marginTop: 10,
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
        }}
      >
        {!isEmpty && (
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            style={{
              border: "none",
              background: !busy ? accent : C.disabledSend,
              color: "#fff",
              borderRadius: 8,
              padding: "7px 14px",
              fontSize: 12.5,
              fontWeight: 600,
              cursor: busy ? "not-allowed" : "pointer",
            }}
          >
            {busy ? "Saving…" : "Confirm"}
          </button>
        )}
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          style={{
            border: "none",
            background: "transparent",
            color: C.muted,
            fontSize: 12.5,
            fontWeight: 600,
            cursor: busy ? "not-allowed" : "pointer",
            padding: 0,
          }}
        >
          Cancel
        </button>
        {error && <span style={{ fontSize: 11.5, color: C.gap }}>{error}</span>}
      </div>
    </div>
  );
}
