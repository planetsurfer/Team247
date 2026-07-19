// Docked footer composer — same pill as landing but slightly smaller
// (input 14.5px, button 34px).
import { C, R, SH, THREAD_PLACEHOLDER } from "../theme";

interface ComposerProps {
  input: string;
  accent: string;
  onInput: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onKey: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onSend: () => void;
}

export function Composer({ input, accent, onInput, onKey, onSend }: ComposerProps) {
  const sendBg = input.trim() ? accent : C.disabledSend;
  return (
    <div style={{ flex: "none", padding: "10px 20px 18px" }}>
      <div style={{ maxWidth: 780, margin: "0 auto", position: "relative" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            background: "#fff",
            border: `1px solid ${C.pillBorder}`,
            borderRadius: R.pill,
            padding: "6px 6px 6px 20px",
            boxShadow: SH.pill,
          }}
        >
          <input
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              fontSize: 14.5,
              background: "transparent",
              color: C.ink,
              padding: "6px 0",
            }}
            placeholder={THREAD_PLACEHOLDER}
            value={input}
            onChange={onInput}
            onKeyDown={onKey}
          />
          <button
            onClick={onSend}
            style={{
              width: 34,
              height: 34,
              borderRadius: "50%",
              border: "none",
              background: sendBg,
              color: "#fff",
              fontSize: 15,
              cursor: "pointer",
              flex: "none",
            }}
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  );
}
