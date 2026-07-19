// Skill loadout card — RPG-style per-skill sliders defaulted to official
// levels, with Baseline/Specialize/Max presets and "Generate agent →".
import { C, R } from "../../theme";
import { SegmentBar } from "../Bar";
import type { SkillState } from "../../types";

interface LoadoutCardProps {
  roleName: string;
  skills: SkillState[];
  running: boolean;
  accent: string;
  onBaseline: () => void;
  onSpecialize: () => void;
  onMax: () => void;
  onDec: (i: number) => void;
  onInc: (i: number) => void;
  onGenerate: () => void;
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
const PRESET: React.CSSProperties = {
  border: `1px solid ${C.pillBorder}`,
  background: "#fff",
  borderRadius: R.preset,
  padding: "5px 11px",
  fontSize: 11.5,
  cursor: "pointer",
  color: "#55524a",
};
const STEP: React.CSSProperties = {
  width: 24,
  height: 24,
  borderRadius: R.stepper,
  border: `1px solid ${C.pillBorder}`,
  background: "#fff",
  cursor: "pointer",
  fontSize: 13,
  color: "#55524a",
  lineHeight: 1,
};

export function LoadoutCard({
  roleName,
  skills,
  running,
  accent,
  onBaseline,
  onSpecialize,
  onMax,
  onDec,
  onInc,
  onGenerate,
}: LoadoutCardProps) {
  const canGenerate = skills.some((k) => k.target > 0);

  return (
    <div style={CARD}>
      <div style={EYEBROW}>Skill loadout</div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          marginTop: 6,
        }}
      >
        <span style={{ fontSize: 15, fontWeight: 650 }}>{roleName}</span>
        <span style={{ display: "flex", gap: 6 }}>
          <button style={PRESET} onClick={onBaseline}>Baseline</button>
          <button style={PRESET} onClick={onSpecialize}>Specialize</button>
          <button style={PRESET} onClick={onMax}>Max</button>
        </span>
      </div>
      <div style={{ fontSize: 12, color: C.muted, marginTop: 4 }}>
        Defaults are the official required levels — meeting the national standard
        is free. Raise a skill to specialize, drop to 0 to leave it out.
      </div>
      <div style={{ marginTop: 12 }}>
        {skills.map((k, i) => {
          const d = k.target - k.off;
          const nameColor = k.target === 0 ? C.faint : C.ink;
          const trackLabel = k.exec ? "✔ executed" : "○ rubric";
          const trackColor = k.exec ? "#0e7a70" : C.rubricAlt;
          const note =
            k.target === 0 ? "off" : d > 0 ? `▲ +${d}` : d < 0 ? `▼ ${d}` : "official";
          const noteColor =
            k.target === 0
              ? C.faint
              : d > 0
                ? accent
                : d < 0
                  ? C.raisedNote
                  : C.dim;
          return (
            <div
              key={k.code}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "9px 0",
                borderBottom: `1px solid ${C.divider}`,
              }}
            >
              <span style={{ width: 186, flex: "none", minWidth: 0 }}>
                <span style={{ display: "block", fontSize: 13, fontWeight: 600, color: nameColor }}>
                  {k.name}
                </span>
                <span
                  className="mono"
                  style={{ display: "block", fontSize: 10, color: C.faint, marginTop: 1 }}
                >
                  {k.code}
                </span>
              </span>
              <span style={{ width: 66, flex: "none", fontSize: 10, color: trackColor }}>
                {trackLabel}
              </span>
              <span style={{ display: "flex", gap: 3, flex: 1 }}>
                <SegmentBar level={k.target} official={k.off} accent={accent} />
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 2, flex: "none" }}>
                <button style={STEP} onClick={() => onDec(i)}>−</button>
                <span
                  className="tnum"
                  style={{ width: 30, textAlign: "center", fontSize: 12.5, fontWeight: 650 }}
                >
                  L{k.target}
                </span>
                <button style={STEP} onClick={() => onInc(i)}>+</button>
              </span>
              <span style={{ width: 58, flex: "none", textAlign: "right", fontSize: 11, fontWeight: 600, color: noteColor }}>
                {note}
              </span>
            </div>
          );
        })}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: 14,
        }}
      >
        <span style={{ fontSize: 11, color: C.faint }}>
          ◆ outline = official level · ✔ executed in sandbox · ○ rubric-judged
        </span>
        <button
          onClick={onGenerate}
          disabled={running || !canGenerate}
          style={{
            border: "none",
            background: accent,
            color: "#fff",
            borderRadius: 10,
            padding: "9px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: running || !canGenerate ? "not-allowed" : "pointer",
            opacity: canGenerate ? 1 : 0.5,
          }}
        >
          {running ? "Generating…" : "Generate agent →"}
        </button>
      </div>
    </div>
  );
}
