// Fixed 52px thread header — the 1D wordmark + right caption.
import { C } from "../theme";
import { HEADER_CAPTION } from "../theme";
import { Logo } from "./Logo";

export function Header(_props: { accent: string }) {
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
      <Logo size={18} />
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
