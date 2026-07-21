// Team recommendation card — matched-to-real-roles list with conf bars,
// selectable rows, and "Set skill loadout →".
import { useState } from "react";
import { C } from "../../theme";
import type { Artifact, RoleRow, UserInputItem, UserInputsSaveState } from "../../types";

interface TeamCardProps {
  roles: RoleRow[];
  artifacts: Artifact[];
  accent: string;
  onToggle: (i: number) => void;
  onConfirm: () => void;
  // Real-inputs intake (Iteration 3 — user-value loop). Omitted teamId (no
  // team context yet) hides the "paste it now" UI entirely — the artifacts
  // panel still renders as before, flow unchanged if this is left untouched.
  teamId?: string;
  userInputsState?: UserInputsSaveState;
  onSaveInputs?: (items: UserInputItem[]) => void;
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
  maxWidth: 640,
};

const ARTIFACT_LABELS: Record<string, string> = {
  sample: "📎 Worked sample",
  blank_format: "▭ Blank format / template",
  past_documents: "🗄 Past documents",
  database: "🗄 Database / system",
  none: "— No external artifact",
};

export function TeamCard({
  roles,
  artifacts,
  accent,
  onToggle,
  onConfirm,
  teamId,
  userInputsState,
  onSaveInputs,
}: TeamCardProps) {
  const n = roles.filter((r) => r.sel).length;
  const hireLine =
    n === 0 ? "Select at least one role." : `You're hiring ${n} role${n > 1 ? "s" : ""}.`;
  // Only show artifacts that are genuinely required (drop the "none" placeholder
  // unless it's the sole entry, in which case it tells the user nothing's needed).
  const needed = (artifacts ?? []).filter((a) => a && a.kind !== "none");

  // Real-inputs intake (Iteration 3) — per-artifact draft text, purely local
  // until Save is clicked (mirrors TryAgentChat's local `draft` convention).
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const canSaveInputs = !!(teamId && onSaveInputs);
  const hasDraft = needed.some((_, i) => (drafts[i] ?? "").trim().length > 0);
  const saving = !!userInputsState?.saving;
  const saved = userInputsState?.saved;
  const saveError = userInputsState?.error;

  const handleSave = () => {
    if (!onSaveInputs) return;
    const items: UserInputItem[] = needed
      .map((a, i) => ({
        kind: a.kind,
        name: ARTIFACT_LABELS[a.kind] ?? a.kind,
        content: (drafts[i] ?? "").trim(),
      }))
      .filter((it) => it.content.length > 0);
    if (items.length === 0) return;
    onSaveInputs(items);
  };

  return (
    <div style={CARD}>
      <div style={EYEBROW}>Matched to real roles · official catalogue</div>
      <div style={{ marginTop: 6, fontSize: 12.5, color: C.muted }}>
        Recommendations are retrieved from the official catalogue — never invented.
        Untick any you don't need.
      </div>
      {needed.length > 0 && (
        <div
          style={{
            marginTop: 10,
            padding: "10px 12px",
            background: "#f6f4ee",
            border: `1px solid ${C.divider}`,
            borderRadius: 10,
          }}
        >
          <div style={{ fontSize: 10.5, fontWeight: 650, letterSpacing: ".8px",
                       textTransform: "uppercase", color: C.dim }}>
            What to provide the build
          </div>
          <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 8 }}>
            {needed.map((a, i) => (
              <div key={i} style={{ fontSize: 12.5, color: "#3c3a33" }}>
                <span style={{ fontWeight: 600 }}>
                  {ARTIFACT_LABELS[a.kind] ?? a.kind}
                </span>
                {a.description ? (
                  <span style={{ color: C.muted }}> — {a.description}</span>
                ) : null}
                {canSaveInputs && (
                  <div style={{ marginTop: 4 }}>
                    <button
                      type="button"
                      onClick={() => setExpanded((s) => ({ ...s, [i]: !s[i] }))}
                      style={{
                        border: "none",
                        background: "none",
                        padding: 0,
                        fontSize: 11.5,
                        fontWeight: 600,
                        color: accent,
                        cursor: "pointer",
                      }}
                    >
                      {expanded[i] ? "Hide" : "Paste it now (optional)"}
                    </button>
                    {expanded[i] && (
                      <textarea
                        value={drafts[i] ?? ""}
                        onChange={(e) =>
                          setDrafts((s) => ({ ...s, [i]: e.target.value }))
                        }
                        placeholder={`Paste your real ${
                          (ARTIFACT_LABELS[a.kind] ?? a.kind).replace(/^\S+\s/, "").toLowerCase()
                        } here…`}
                        rows={4}
                        style={{
                          width: "100%",
                          marginTop: 6,
                          boxSizing: "border-box",
                          fontSize: 12.5,
                          fontFamily: "inherit",
                          padding: 8,
                          borderRadius: 8,
                          border: `1px solid ${C.divider}`,
                          resize: "vertical",
                        }}
                      />
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
          {canSaveInputs && (
            <div
              style={{
                marginTop: 10,
                display: "flex",
                alignItems: "center",
                gap: 10,
                flexWrap: "wrap",
              }}
            >
              <button
                type="button"
                onClick={handleSave}
                disabled={!hasDraft || saving}
                style={{
                  border: `1px solid ${hasDraft ? accent : C.checkboxOffBorder}`,
                  background: "#fff",
                  color: hasDraft ? accent : C.dim,
                  borderRadius: 8,
                  padding: "5px 12px",
                  fontSize: 11.5,
                  fontWeight: 600,
                  cursor: !hasDraft || saving ? "not-allowed" : "pointer",
                  opacity: saving ? 0.6 : 1,
                }}
              >
                {saving ? "Saving…" : "Save"}
              </button>
              {saved && (
                <span style={{ fontSize: 11.5, color: C.dim }}>
                  ✓ {saved.count} input{saved.count === 1 ? "" : "s"} added ·{" "}
                  {saved.bytes.toLocaleString()} bytes
                </span>
              )}
              {saveError && (
                <span style={{ fontSize: 11.5, color: C.gap }}>{saveError}</span>
              )}
            </div>
          )}
        </div>
      )}
      <div style={{ marginTop: 10 }}>
        {roles.map((r, i) => (
          <div
            key={i}
            onClick={() => onToggle(i)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "11px 0",
              borderBottom: `1px solid ${C.divider}`,
              cursor: "pointer",
            }}
          >
            <span
              style={{
                width: 18,
                height: 18,
                borderRadius: 5,
                flex: "none",
                display: "grid",
                placeItems: "center",
                fontSize: 12,
                color: "#fff",
                background: r.sel ? accent : "#fff",
                border: `1px solid ${r.sel ? accent : C.checkboxOffBorder}`,
              }}
            >
              {r.sel ? "✓" : ""}
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: "block", fontSize: 14, fontWeight: 600 }}>
                {r.name}
              </span>
              <span style={{ display: "block", fontSize: 11.5, color: C.dim, marginTop: 2 }}>
                {r.sector}
                {r.matched ? ` · matched on: ${r.matched}` : ""}
              </span>
            </span>
            {r.conf != null ? (
              <>
                <span
                  style={{
                    width: 64,
                    height: 6,
                    borderRadius: 3,
                    background: C.barTrack,
                    flex: "none",
                    overflow: "hidden",
                  }}
                >
                  <span
                    style={{
                      display: "block",
                      height: 6,
                      borderRadius: 3,
                      background: accent,
                      width: `${r.conf}%`,
                    }}
                  />
                </span>
                <span
                  className="tnum"
                  style={{ width: 34, fontSize: 12, fontWeight: 600, textAlign: "right" }}
                >
                  {r.conf}%
                </span>
              </>
            ) : (
              <span style={{ width: 34 }} />
            )}
          </div>
        ))}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: 14,
        }}
      >
        <span style={{ fontSize: 12.5, color: C.muted }}>{hireLine}</span>
        <button
          onClick={onConfirm}
          disabled={n === 0}
          style={{
            border: "none",
            background: accent,
            color: "#fff",
            borderRadius: 10,
            padding: "9px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: n === 0 ? "not-allowed" : "pointer",
            opacity: n === 0 ? 0.5 : 1,
          }}
        >
          Set skill loadout →
        </button>
      </div>
    </div>
  );
}
