// Fixed 52px thread header — accent square + Team247 + right caption.
import { C } from "../theme";
import { HEADER_CAPTION } from "../theme";

export function Header({ accent }: { accent: string }) {
  return (
    <div
      style={{
        height: 52,
        flex: "none",
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "0 22px",
        borderBottom: `1px solid ${C.headerDivider}`,
      }}
    >
      <span style={{ width: 10, height: 10, borderRadius: 3, background: accent }} />
      <span style={{ fontSize: 14, fontWeight: 650 }}>Team247</span>
      <span
        style={{
          marginLeft: "auto",
          fontSize: 11,
          color: C.dim,
          letterSpacing: ".4px",
        }}
      >
        {HEADER_CAPTION}
      </span>
    </div>
  );
}
