// "How to install" popover (Iteration 5 — one-click export). Small ⓘ button
// that opens a compact 3-tab panel with static, verified install copy for
// Claude Code / Claude.ai / Codex-or-other. Shared between DeliverCard's
// action row and the gallery detail panel (Landing.tsx) — same copy either
// place, per the iteration 5 spec.
import { useEffect, useRef, useState } from "react";
import { C, R, SH } from "../theme";

type TabKey = "claude-code" | "claude-ai" | "codex";

const TABS: { key: TabKey; label: string; body: string }[] = [
  {
    key: "claude-code",
    label: "Claude Code",
    body:
      "Unzip into ~/.claude/skills/ (keep the folder layout: <agent-name>/SKILL.md). " +
      "For a single project, use .claude/skills/ in the repo instead. No restart " +
      "needed — Claude Code picks it up in-session. Invoke with /<agent-name> or " +
      "just describe the task and it triggers automatically. Tip: folder name = " +
      "the command; keep hyphens, no spaces.",
  },
  {
    key: "claude-ai",
    label: "Claude.ai",
    body:
      "Personal SKILL.md files don't load on claude.ai directly. Instead: use " +
      "'Copy as prompt' and paste it into a Project's instructions — or commit " +
      ".claude/skills/<agent-name>/SKILL.md to a repo you use with Claude.",
  },
  {
    key: "codex",
    label: "Codex / other",
    body:
      "Use 'Copy as prompt' and paste as the system prompt (or top instruction) " +
      "in your harness. The agent's inputs/deliverable contract and your " +
      "provided data travel with it.",
  },
];

export function InstallPopover({ accent }: { accent: string }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabKey>("claude-code");
  const wrapRef = useRef<HTMLDivElement>(null);

  // dismiss on outside click
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const active = TABS.find((t) => t.key === tab) ?? TABS[0];

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "inline-flex" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="How to install"
        title="How to install"
        style={{
          border: `1px solid ${C.pillBorder}`,
          background: "#fff",
          color: C.muted,
          borderRadius: "50%",
          width: 32,
          height: 32,
          fontSize: 13,
          fontWeight: 650,
          cursor: "pointer",
          flex: "none",
        }}
      >
        ⓘ
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="How to install"
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            left: 0,
            zIndex: 20,
            width: 340,
            maxWidth: "min(380px, 90vw)",
            background: "#fff",
            border: `1px solid ${C.cardBorder}`,
            borderRadius: R.card,
            boxShadow: SH.dropdown,
            padding: 14,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 10,
            }}
          >
            <div style={{ fontSize: 12.5, fontWeight: 650, color: C.ink }}>How to install</div>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close"
              style={{
                border: "none",
                background: "transparent",
                color: C.faint,
                fontSize: 14,
                padding: "2px 4px",
                cursor: "pointer",
                flex: "none",
              }}
            >
              ✕
            </button>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                style={{
                  border: "none",
                  background: tab === t.key ? accent : C.chipBg,
                  color: tab === t.key ? "#fff" : "#3c3a33",
                  borderRadius: R.chip,
                  padding: "5px 10px",
                  fontSize: 11.5,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div style={{ marginTop: 10, fontSize: 12, lineHeight: 1.55, color: C.muted }}>
            {active.body}
          </div>
        </div>
      )}
    </div>
  );
}
