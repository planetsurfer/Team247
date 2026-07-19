// Deliver card — "Role-readiness X% → Y%" with two-column before/after bars
// and Download / Copy / Adjust actions. Two-track honest: rubric skills show
// ○ and are never blended into the headline.
import { C } from "../../theme";
import { PairBar } from "../Bar";
import type { SkillState } from "../../types";
import { readiness } from "../../useChat";

interface DeliverCardProps {
  roleName: string;
  skills: SkillState[];
  copied: boolean;
  accent: string;
  onDownload: () => void;
  onCopy: () => void;
  onAdjust: () => void;
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

export function DeliverCard({
  roleName,
  skills,
  copied,
  accent,
  onDownload,
  onCopy,
  onAdjust,
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
      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button
          onClick={onDownload}
          style={{
            border: "none",
            background: accent,
            color: "#fff",
            borderRadius: 10,
            padding: "9px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          ⬇ Download agent spec
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
    </div>
  );
}
