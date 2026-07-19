// Build & prove card — step lines + per-skill animated bars + readiness %.
// Animation driven from prove.t (client rAF clock) gated by the verify job
// status; bars fill toward real base/fin values once the job returns them.
import { C } from "../../theme";
import { FillBar } from "../Bar";
import type { ProveState, SkillState } from "../../types";
import { readiness } from "../../useChat";

interface ProveCardProps {
  prove: ProveState | null;
  skills: SkillState[]; // already-filtered active (target>0) by caller, or full
  accent: string;
  onRetry?: () => void;
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
  maxWidth: 660,
};

function stepIcon(done: boolean): { ch: string; color: string } {
  return done ? { ch: "✓", color: C.success } : { ch: "•", color: C.faint };
}

export function ProveCard({ prove, skills, accent, onRetry }: ProveCardProps) {
  const active = skills.filter((k) => k.target > 0);
  const t = prove?.t ?? 0;

  // display phase thresholds (mirror the prototype timings)
  const fillP = Math.max(0, Math.min(1, (t - 500) / 1800));
  const riseP = Math.max(0, Math.min(1, (t - 3100) / 1500));
  const specDone = t >= 500;
  const barsDone = t >= 2300;
  const refining = t >= 2600;
  const refineDone = t >= 5300;

  const failed = prove?.status === "failed";

  // readiness: exec-track only (never blended with rubric — verify_service.py)
  const execActive = active.filter((k) => k.track === "exec");
  const baseReady = execActive.length
    ? Math.round(execActive.reduce((a, k) => a + (k.base ?? 0), 0) / execActive.length)
    : 0;
  const finReady = execActive.length
    ? Math.round(execActive.reduce((a, k) => a + (k.fin ?? 0), 0) / execActive.length)
    : 0;
  // silence unused-readyness warning if both null
  void readiness;

  const read =
    t < 3100
      ? Math.round(baseReady * fillP)
      : Math.round(baseReady + (finReady - baseReady) * riseP);
  const readinessLabel =
    t < 3100 ? (barsDone ? "(baseline)" : "") : refineDone ? "(refined)" : "(refining…)";
  const showBars = t > 500;
  const spec = stepIcon(specDone);
  const base = stepIcon(barsDone);
  const refine = stepIcon(refineDone);

  return (
    <div style={CARD}>
      <div style={EYEBROW}>Building &amp; proving</div>

      <div style={{ display: "flex", gap: 8, fontSize: 13, marginTop: 12 }}>
        <span style={{ width: 16, color: spec.color }}>{spec.ch}</span>
        <span>Agent spec generated from the role + your loadout</span>
      </div>

      {showBars && !failed && (
        <>
          <div style={{ display: "flex", gap: 8, fontSize: 13, marginTop: 10 }}>
            <span style={{ width: 16, color: base.color }}>{base.ch}</span>
            <span>Baseline — each skill runs in an isolated sandbox, in parallel</span>
          </div>
          <div style={{ margin: "10px 0 0 24px" }}>
            {active.map((k) => {
              const v =
                t < 3100
                  ? (k.base ?? 0) * fillP
                  : (k.base ?? 0) + ((k.fin ?? 0) - (k.base ?? 0)) * riseP;
              const color = k.exec ? accent : C.rubricFill;
              const gap = barsDone && t < 4600 && (k.base ?? 0) < 60 ? "⚠ gap" : "";
              return (
                <div
                  key={k.code}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "5px 0" }}
                >
                  <span style={{ width: 172, flex: "none", fontSize: 12, color: "#55524a" }}>
                    {k.name}
                    {k.target > k.off ? ` (L${k.target})` : ""}
                  </span>
                  <FillBar pct={v} color={color} />
                  <span
                    className="mono"
                    style={{ width: 36, flex: "none", fontSize: 11.5, textAlign: "right" }}
                  >
                    {Math.round(v)}%
                  </span>
                  <span style={{ width: 46, flex: "none", fontSize: 10.5, color: C.gap }}>
                    {gap}
                  </span>
                </div>
              );
            })}
            <div style={{ fontSize: 12.5, fontWeight: 650, marginTop: 8 }}>
              Role-readiness: {read}%{" "}
              <span style={{ fontWeight: 400, color: C.dim }}>{readinessLabel}</span>
            </div>
          </div>
        </>
      )}

      {refining && !failed && (
        <div style={{ display: "flex", gap: 8, fontSize: 13, marginTop: 12 }}>
          <span style={{ width: 16, color: refine.color }}>{refine.ch}</span>
          <span>Refining — rewriting the agent's guidance on each gap, re-running</span>
        </div>
      )}

      {failed && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12.5, color: C.gap }}>
            {prove?.error ?? "verify failed"}
          </div>
          {onRetry && (
            <button
              onClick={onRetry}
              style={{
                marginTop: 8,
                border: `1px solid ${C.pillBorder}`,
                background: "#fff",
                color: "#3c3a33",
                borderRadius: 10,
                padding: "7px 14px",
                fontSize: 12.5,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              ⟳ Retry verify
            </button>
          )}
        </div>
      )}
    </div>
  );
}
