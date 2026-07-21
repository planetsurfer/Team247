// Deliver card — "Role-readiness X% → Y%" with two-column before/after bars
// and Download / Copy / Adjust actions. Two-track honest: rubric skills show
// ○ and are never blended into the headline.
import { C } from "../../theme";
import { PairBar } from "../Bar";
import type { FeedbackState, SkillState } from "../../types";
import { readiness } from "../../useChat";

interface DeliverCardProps {
  roleName: string;
  skills: SkillState[];
  copied: boolean;
  accent: string;
  bundleBusy: boolean;
  onDownload: () => void;
  onDownloadBundle: () => void;
  onCopy: () => void;
  onAdjust: () => void;
  // Iteration 1 (beta feedback instrumentation) — omitted teamId hides the
  // row entirely (e.g. no team context yet); otherwise it fires once per
  // team, keyed by teamId in useChat's feedbackByTeam.
  teamId?: string;
  feedback?: FeedbackState;
  onFeedbackThumb?: (verdict: "up" | "down") => void;
  onFeedbackCommentChange?: (comment: string) => void;
  onFeedbackCommentSubmit?: () => void;
  onFeedbackDismiss?: () => void;
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
  maxWidth: 700,
};

// Slim dismissible feedback row (Iteration 1 — user-value loop). Three
// states, driven entirely by `feedback` (useChat's per-team FeedbackState):
//   1. dismissed          -> render nothing
//   2. no verdict yet     -> "Did this match what you needed?" + thumbs + ✕
//   3. verdict set        -> thank-you + optional one-line comment input
//      (hides the input once commentSubmitted, to match "fires once per team")
function FeedbackRow({
  feedback,
  onThumb,
  onCommentChange,
  onCommentSubmit,
  onDismiss,
}: {
  feedback?: FeedbackState;
  onThumb: (v: "up" | "down") => void;
  onCommentChange: (v: string) => void;
  onCommentSubmit: () => void;
  onDismiss: () => void;
}) {
  if (feedback?.dismissed) return null;

  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginTop: 14,
    paddingTop: 12,
    borderTop: `1px solid ${C.divider}`,
    fontSize: 12,
    color: C.muted,
  };
  const thumbStyle: React.CSSProperties = {
    border: `1px solid ${C.pillBorder}`,
    background: "#fff",
    borderRadius: 8,
    padding: "3px 9px",
    fontSize: 13,
    lineHeight: 1.4,
    cursor: "pointer",
  };

  if (feedback?.verdict) {
    const draft = feedback.comment ?? "";
    return (
      <div style={rowStyle}>
        <span>Thanks for the feedback.</span>
        {!feedback.commentSubmitted ? (
          <>
            <input
              value={draft}
              onChange={(e) => onCommentChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onCommentSubmit();
              }}
              placeholder="Anything to add?"
              style={{
                flex: 1,
                minWidth: 100,
                border: `1px solid ${C.pillBorder}`,
                borderRadius: 8,
                padding: "4px 8px",
                fontSize: 12,
                color: C.ink,
                background: "#fff",
              }}
            />
            <button
              onClick={onCommentSubmit}
              disabled={!draft.trim()}
              style={{
                ...thumbStyle,
                opacity: draft.trim() ? 1 : 0.5,
                cursor: draft.trim() ? "pointer" : "default",
              }}
            >
              Send
            </button>
          </>
        ) : (
          <span style={{ color: C.success }}>✓ noted</span>
        )}
      </div>
    );
  }

  return (
    <div style={rowStyle}>
      <span style={{ flex: 1 }}>Did this match what you needed?</span>
      <button style={thumbStyle} onClick={() => onThumb("up")} aria-label="Thumbs up — yes">
        👍
      </button>
      <button style={thumbStyle} onClick={() => onThumb("down")} aria-label="Thumbs down — no">
        👎
      </button>
      <button
        onClick={onDismiss}
        aria-label="Dismiss feedback prompt"
        style={{
          border: "none",
          background: "transparent",
          color: C.faint,
          fontSize: 13,
          padding: "3px 6px",
          cursor: "pointer",
        }}
      >
        ✕
      </button>
    </div>
  );
}

export function DeliverCard({
  roleName,
  skills,
  copied,
  accent,
  bundleBusy,
  onDownload,
  onDownloadBundle,
  onCopy,
  onAdjust,
  teamId,
  feedback,
  onFeedbackThumb,
  onFeedbackCommentChange,
  onFeedbackCommentSubmit,
  onFeedbackDismiss,
}: DeliverCardProps) {
  const active = skills.filter((k) => k.target > 0);
  const r = readiness(active);
  const base = r.base ?? 0;
  const fin = r.fin ?? 0;

  return (
    <div style={CARD}>
      <div style={EYEBROW}>Delivered · {roleName}</div>
      <div style={{ fontSize: 21, fontWeight: 650, marginTop: 10 }}>
        Role-readiness{" "}
        <span style={{ color: C.dim }}>{base}%</span>{" "}
        <span style={{ color: C.faint, fontWeight: 400 }}>→</span>{" "}
        <span style={{ color: accent }}>{fin}%</span>
      </div>
      <div
        style={{
          display: "flex",
          gap: 10,
          fontSize: 10.5,
          color: C.dim,
          margin: "14px 0 4px 0",
        }}
      >
        <span style={{ width: 172, flex: "none" }} />
        <span style={{ flex: 1 }}>baseline</span>
        <span style={{ flex: 1 }}>refined</span>
      </div>
      <div>
        {active.map((k) => (
          <div
            key={k.code}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "6px 0",
              borderBottom: `1px solid ${C.divider}`,
            }}
          >
            <span style={{ width: 172, flex: "none", fontSize: 12.5, color: "#3c3a33" }}>
              {k.name}{" "}
              <span style={{ fontSize: 10, color: C.faint }}>{k.exec ? "" : "○"}</span>
            </span>
            <PairBar
              basePct={k.base ?? 0}
              finPct={k.fin ?? 0}
              accent={accent}
            />
          </div>
        ))}
      </div>
      <div style={{ fontSize: 11.5, color: C.muted, marginTop: 12, lineHeight: 1.5 }}>
        Every filled bar is code this agent wrote that actually ran in an
        isolated sandbox, graded against the official K&amp;A rubric. ○ skills
        are rubric-judged and never blended with executed scores.
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
        <button
          onClick={onDownloadBundle}
          disabled={bundleBusy}
          style={{
            border: "none",
            background: accent,
            color: "#fff",
            borderRadius: 10,
            padding: "9px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: bundleBusy ? "wait" : "pointer",
            opacity: bundleBusy ? 0.7 : 1,
          }}
        >
          {bundleBusy ? "⏳ Preparing agent…" : "⬇ Drop-in agent (SKILL.md)"}
        </button>
        <button
          onClick={onDownload}
          style={{
            border: `1px solid ${C.pillBorder}`,
            background: "#fff",
            color: "#3c3a33",
            borderRadius: 10,
            padding: "9px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          ⬇ Agent spec
        </button>
        <button
          onClick={onCopy}
          style={{
            border: `1px solid ${C.pillBorder}`,
            background: "#fff",
            color: "#3c3a33",
            borderRadius: 10,
            padding: "9px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          {copied ? "✓ Copied" : "⧉ Copy"}
        </button>
        <button
          onClick={onAdjust}
          style={{
            border: `1px solid ${C.pillBorder}`,
            background: "#fff",
            color: "#3c3a33",
            borderRadius: 10,
            padding: "9px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          ⟳ Adjust loadout
        </button>
      </div>
      {teamId && (
        <FeedbackRow
          feedback={feedback}
          onThumb={(v) => onFeedbackThumb?.(v)}
          onCommentChange={(v) => onFeedbackCommentChange?.(v)}
          onCommentSubmit={() => onFeedbackCommentSubmit?.()}
          onDismiss={() => onFeedbackDismiss?.()}
        />
      )}
    </div>
  );
}
