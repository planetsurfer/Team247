// Landing (empty) state — centered input pill with role autocomplete dropdown,
// task suggestion chips, and role chips. Everything after the first submit
// happens inline in the thread (no page nav).
import { useEffect, useMemo, useState } from "react";
import { C, R, SH, TASK_CHIPS, ROLE_CHIPS, copy, type Theme } from "../theme";
import { catalogSearch, isApiError } from "../api";
import { Logo } from "./Logo";
import type { CatalogItem } from "../types";

interface LandingProps {
  theme: Theme;
  input: string;
  onInput: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onKey: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  onSend: (text?: string) => void;
  onPickRole: (name: string) => void;
  // beta-access gate (closed beta — PRODUCTION_ROADMAP.md P0 #1): when true,
  // the task input is replaced by a token-entry form.
  gate?: boolean;
  gateError?: string;
  onSubmitBetaToken?: (token: string) => void;
}

const TASK_CHIP: React.CSSProperties = {
  border: `1px solid ${C.pillBorder}`,
  background: "#fff",
  borderRadius: R.chip,
  padding: "8px 14px",
  fontSize: 12.5,
  color: "#55524a",
  cursor: "pointer",
};
const ROLE_CHIP: React.CSSProperties = {
  border: "none",
  background: C.chipBg,
  borderRadius: R.chip,
  padding: "7px 13px",
  fontSize: 12.5,
  fontWeight: 550,
  color: "#3c3a33",
  cursor: "pointer",
};

export function Landing({
  theme,
  input,
  onInput,
  onKey,
  onSend,
  onPickRole,
  gate,
  gateError,
  onSubmitBetaToken,
}: LandingProps) {
  const c = copy(theme.tone);
  const [matches, setMatches] = useState<CatalogItem[]>([]);
  const [betaTokenInput, setBetaTokenInput] = useState("");
  const q = input.trim().toLowerCase();

  // role autocomplete: only on landing (caller guarantees messages empty) and
  // only for query length > 1; substring over the catalog, max 3 (prototype rule).
  // Skipped entirely while the beta gate is up — the task input isn't shown.
  useEffect(() => {
    if (gate || q.length <= 1) {
      setMatches([]);
      return;
    }
    let cancelled = false;
    const id = setTimeout(async () => {
      const r = await catalogSearch(input.trim(), 10);
      if (cancelled || isApiError(r)) return;
      // substring match + max 3, like the prototype
      const sub = r.items.filter((it) => it.role.toLowerCase().includes(q)).slice(0, 3);
      setMatches(sub);
    }, 120);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
  }, [q, input, gate]);

  const showTasks = theme.suggestions === "both" || theme.suggestions === "tasks";
  const showRoles = theme.suggestions === "both" || theme.suggestions === "roles";
  const sendBg = input.trim() ? theme.accent : C.disabledSend;

  const pill: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    background: "#fff",
    border: `1px solid ${C.pillBorder}`,
    borderRadius: R.pill,
    padding: "8px 8px 8px 20px",
    boxShadow: SH.pill,
  };

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div style={{ marginBottom: 36 }}>
        <Logo size={38} />
      </div>
      <h1
        style={{
          margin: 0,
          fontSize: 27,
          fontWeight: 600,
          letterSpacing: "-.3px",
          textAlign: "center",
        }}
      >
        {c.title}
      </h1>
      <p
        style={{
          margin: "12px 0 0",
          fontSize: 15,
          lineHeight: 1.55,
          color: C.muted,
          maxWidth: 540,
          textAlign: "center",
          textWrap: "pretty",
        }}
      >
        {gate
          ? "Team247 is in closed beta. Paste your beta access token to continue."
          : c.sub}
      </p>
      {gate ? (
        <div style={{ width: "100%", maxWidth: 480, marginTop: 32 }}>
          <div style={pill}>
            <input
              type="password"
              style={{
                flex: 1,
                border: "none",
                outline: "none",
                fontSize: 15.5,
                background: "transparent",
                color: C.ink,
                padding: "6px 0",
              }}
              placeholder="Paste your beta access token"
              value={betaTokenInput}
              onChange={(e) => setBetaTokenInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && betaTokenInput.trim()) {
                  onSubmitBetaToken?.(betaTokenInput.trim());
                }
              }}
            />
            <button
              onClick={() => betaTokenInput.trim() && onSubmitBetaToken?.(betaTokenInput.trim())}
              disabled={!betaTokenInput.trim()}
              style={{
                width: 36,
                height: 36,
                borderRadius: "50%",
                border: "none",
                background: betaTokenInput.trim() ? theme.accent : C.disabledSend,
                color: "#fff",
                fontSize: 16,
                cursor: betaTokenInput.trim() ? "pointer" : "default",
                flex: "none",
              }}
            >
              ↑
            </button>
          </div>
          {gateError && (
            <p
              style={{
                margin: "10px 0 0",
                fontSize: 12.5,
                color: C.gap,
                textAlign: "center",
              }}
            >
              {gateError}
            </p>
          )}
        </div>
      ) : (
        <>
          <div style={{ width: "100%", maxWidth: 660, marginTop: 32, position: "relative" }}>
            <div style={pill}>
              <input
                style={{
                  flex: 1,
                  border: "none",
                  outline: "none",
                  fontSize: 15.5,
                  background: "transparent",
                  color: C.ink,
                  padding: "6px 0",
                }}
                placeholder={c.placeholder}
                value={input}
                onChange={onInput}
                onKeyDown={onKey}
              />
              <button
                onClick={() => onSend()}
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: "50%",
                  border: "none",
                  background: sendBg,
                  color: "#fff",
                  fontSize: 16,
                  cursor: "pointer",
                  flex: "none",
                }}
              >
                ↑
              </button>
            </div>
            {matches.length > 0 && (
              <div
                style={{
                  position: "absolute",
                  left: 12,
                  right: 12,
                  top: "100%",
                  marginTop: 6,
                  background: "#fff",
                  border: `1px solid ${C.pillBorder}`,
                  borderRadius: R.autocomplete,
                  boxShadow: SH.dropdown,
                  overflow: "hidden",
                  zIndex: 5,
                }}
              >
                {matches.map((m) => (
                  <div
                    key={m.role_id}
                    onClick={() => onPickRole(m.role)}
                    style={{
                      display: "flex",
                      alignItems: "baseline",
                      gap: 10,
                      padding: "11px 16px",
                      cursor: "pointer",
                      fontSize: 14,
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.background = C.rowHover)
                    }
                    onMouseLeave={(e) => (e.currentTarget.style.background = "#fff")}
                  >
                    <span style={{ fontWeight: 600 }}>{m.role}</span>
                    <span style={{ fontSize: 11.5, color: C.dim }}>
                      {m.sector} · {m.track}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {showTasks && (
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 8,
                justifyContent: "center",
                maxWidth: 620,
                marginTop: 26,
              }}
            >
              {TASK_CHIPS.map((label) => (
                <button key={label} style={TASK_CHIP} onClick={() => onSend(label)}>
                  {label}
                </button>
              ))}
            </div>
          )}
          {showRoles && (
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 8,
                justifyContent: "center",
                alignItems: "center",
                maxWidth: 620,
                marginTop: 12,
              }}
            >
              <span style={{ fontSize: 11.5, color: C.dim }}>or pick a role:</span>
              {ROLE_CHIPS.map((label) => (
                <button key={label} style={ROLE_CHIP} onClick={() => onPickRole(label)}>
                  {label}
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
