// The Team247 "1D" mark — "Team" + steel 24/7 stack (Barlow Condensed).
// Source of truth: claude.ai design "Team247 Logo.dc.html", variant D · 24/7 STACK.
import { C } from "../theme";

const STEEL = "#4d7dad";

export function Logo({ size = 20 }: { size?: number }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.42,
        height: size,
        fontFamily: '"Barlow Condensed", "Arial Narrow", Arial, sans-serif',
        userSelect: "none",
      }}
    >
      <span
        style={{
          fontSize: size,
          fontWeight: 700,
          letterSpacing: ".4px",
          color: C.ink,
          lineHeight: 1,
        }}
      >
        Team
      </span>
      <span style={{ width: 1.5, height: size * 0.92, background: STEEL, opacity: 0.8 }} />
      <span
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          color: STEEL,
          fontWeight: 600,
          fontSize: size * 0.46,
          lineHeight: 1.02,
          letterSpacing: ".6px",
        }}
      >
        <span>24</span>
        <span>7</span>
      </span>
    </span>
  );
}
