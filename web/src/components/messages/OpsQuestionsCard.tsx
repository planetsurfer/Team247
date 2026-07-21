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
import type { OpsQuestionsState } from "../../types";

interface OpsQuestionsCardProps {
  ops: OpsQuestionsState;
  accent: string;
  onAnswer: (idx: number, text: string) => void;
  onSave: () => void;
  onDismiss: () => void;
  // Iteration 3 stub (paste a meeting transcript instead) — invisible until
  // that iteration wires a real handler in. Defaults to false/omitted so
  // nothing renders unless a future caller explicitly opts in.
  enableTranscript?: boolean;
  onTranscriptClick?: () => void;
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
  onTranscriptClick,
}: OpsQuestionsCardProps) {
  const [hovered, setHovered] = useState(false);
  if (ops.dismissed || ops.questions.length === 0) return null;

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
            ✓ {ops.savedCount ?? 0} answer{(ops.savedCount ?? 0) === 1 ? "" : "s"} saved — they'll
            be baked into your agents
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
      {enableTranscript && (
        <div style={{ marginTop: 10 }}>
          <button
            type="button"
            onClick={onTranscriptClick}
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
        </div>
      )}
    </div>
  );
}
