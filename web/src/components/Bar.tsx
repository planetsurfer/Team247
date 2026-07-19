// Shared bar primitives — segment bars (loadout), single fill bars (prove),
// and baseline+refined paired bars (deliver). Pixel values from the handoff.
import type { CSSProperties } from "react";
import { C, R } from "../theme";

interface SegmentBarProps {
  level: number; // filled up to this level (1..6)
  official: number; // official level — gets the outline ring
  accent: string;
}
// Loadout: six 16×8px segments, radius 2.5, filled accent up to target,
// empty #e8e5dc; the official-level segment carries the ring shadow.
export function SegmentBar({ level, official, accent }: SegmentBarProps) {
  return (
    <span style={{ display: "flex", gap: 3 }}>
      {[1, 2, 3, 4, 5, 6].map((l) => {
        const filled = l <= level;
        const isOfficial = l === official;
        const s: CSSProperties = {
          width: 16,
          height: 8,
          borderRadius: R.segment,
          background: filled ? accent : C.segmentEmpty,
          boxShadow: isOfficial ? "0 0 0 1.5px rgba(28,28,26,.38)" : "none",
        };
        return <span key={l} style={s} />;
      })}
    </span>
  );
}

interface FillBarProps {
  pct: number; // 0..100
  color: string;
  width?: number; // px of the track (default flex:1)
  height?: number; // default 8
  radius?: number; // default R.bar (4)
}
// Single fill bar used in the prove card.
export function FillBar({ pct, color, height = 8, radius = R.bar }: FillBarProps) {
  return (
    <span
      style={{
        flex: 1,
        height,
        borderRadius: radius,
        background: C.barTrack,
        overflow: "hidden",
      }}
    >
      <span
        style={{
          display: "block",
          height,
          borderRadius: radius,
          background: color,
          width: `${Math.max(0, Math.min(100, pct))}%`,
          transition: "width .3s ease",
        }}
      />
    </span>
  );
}

interface PairBarProps {
  basePct: number; // baseline fill
  finPct: number; // refined fill
  accent: string;
}
// Deliver: baseline bar (#c6c1b2) + refined bar (accent), each 7px radius 3.5.
export function PairBar({ basePct, finPct, accent }: PairBarProps) {
  const cell = (pct: number, color: string, bold = false) => (
    <span
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      <span
        style={{
          flex: 1,
          height: 7,
          borderRadius: R.baselineBar,
          background: C.barTrack,
          overflow: "hidden",
        }}
      >
        <span
          style={{
            display: "block",
            height: 7,
            background: color,
            width: `${Math.max(0, Math.min(100, pct))}%`,
          }}
        />
      </span>
      <span
        className="mono"
        style={{
          width: 30,
          fontSize: 11,
          textAlign: "right",
          fontWeight: bold ? 600 : 400,
        }}
      >
        {Math.round(pct)}%
      </span>
    </span>
  );
  return (
    <>
      {cell(basePct, C.baselineBar)}
      {cell(finPct, accent, true)}
    </>
  );
}
