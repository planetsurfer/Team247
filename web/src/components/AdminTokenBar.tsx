// Slim row prompting for the admin token (needed for the verify step).
// Hidden when a token is already stored; reappears on a 401 (tokenRejected).
import { useState } from "react";
import { C } from "../theme";

interface AdminTokenBarProps {
  rejected: boolean;
  onSave: (t: string) => void;
}

export function AdminTokenBar({ rejected, onSave }: AdminTokenBarProps) {
  const [val, setVal] = useState("");
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 22px",
        background: "#f6f4ee",
        borderBottom: `1px solid ${C.headerDivider}`,
        fontSize: 11.5,
        color: C.muted,
      }}
    >
      <span>
        {rejected
          ? "Admin token rejected — re-enter to prove skills in the sandbox:"
          : "Add an admin token to prove skills in the sandbox:"}
      </span>
      <input
        type="password"
        value={val}
        onChange={(e) => setVal(e.target.value)}
        placeholder="APP_ADMIN_TOKEN"
        style={{
          flex: 1,
          maxWidth: 360,
          border: `1px solid ${C.pillBorder}`,
          borderRadius: 8,
          padding: "4px 10px",
          fontSize: 11.5,
          fontFamily: "ui-monospace, Menlo, monospace",
          background: "#fff",
        }}
      />
      <button
        onClick={() => onSave(val.trim())}
        disabled={!val.trim()}
        style={{
          border: "none",
          background: val.trim() ? C.ink : C.disabledSend,
          color: "#fff",
          borderRadius: 8,
          padding: "5px 12px",
          fontSize: 11.5,
          fontWeight: 600,
          cursor: val.trim() ? "pointer" : "default",
        }}
      >
        Save
      </button>
    </div>
  );
}
