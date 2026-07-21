// Landing (empty) state — centered input pill with role autocomplete dropdown,
// task suggestion chips, role chips, and a starter gallery of proven agents.
// Everything after the first submit happens inline in the thread (no page nav).
import { useEffect, useRef, useState } from "react";
import {
  C,
  COPY_AS_PROMPT_PREAMBLE,
  R,
  SH,
  TASK_CHIPS,
  ROLE_CHIPS,
  copy,
  type Theme,
} from "../theme";
import { catalogSearch, galleryDetail, galleryList, isApiError, skillBundlesZip } from "../api";
import { Logo } from "./Logo";
import { InstallPopover } from "./InstallPopover";
import { TryAgentChat } from "./messages/TryAgentChat";
import type { CatalogItem, ChatThreadState, GalleryDetail, GalleryListItem } from "../types";

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
  // Starter gallery (Iteration 4 — user-value loop). GET /api/gallery is
  // open, so the card grid renders regardless of `gate`; opening a card's
  // detail (GET /api/gallery/{slug}) and its actions (chat / download) are
  // beta-gated, same convention as the rest of the app: adminToken wins,
  // else the stored beta token (see api.ts's apiVerify).
  adminToken?: string;
  chatByAgent?: Record<string, ChatThreadState>;
  onAgentChatSend?: (teamId: string, agentId: string, text: string) => void;
  onCustomizeUseCase?: (useCase: string) => void;
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
const GALLERY_CARD: React.CSSProperties = {
  textAlign: "left",
  border: `1px solid ${C.pillBorder}`,
  background: "#fff",
  borderRadius: R.card,
  padding: "12px 14px",
  cursor: "pointer",
  width: 208,
  flex: "0 0 auto",
  boxShadow: SH.pill,
};
const DETAIL_PANEL: React.CSSProperties = {
  marginTop: 16,
  border: `1px solid ${C.cardBorder}`,
  background: C.cardBg,
  borderRadius: R.card,
  padding: "16px 18px",
  textAlign: "left",
};
const BUNDLE_PRE: React.CSSProperties = {
  marginTop: 8,
  background: C.pageBg,
  border: `1px solid ${C.divider}`,
  borderRadius: 10,
  padding: "10px 12px",
  fontSize: 11.5,
  lineHeight: 1.5,
  whiteSpace: "pre-wrap",
  maxHeight: 320,
  overflowY: "auto",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};
const secondaryBtn: React.CSSProperties = {
  border: `1px solid ${C.pillBorder}`,
  background: "#fff",
  color: "#3c3a33",
  borderRadius: 10,
  padding: "9px 16px",
  fontSize: 13,
  fontWeight: 600,
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
  adminToken,
  chatByAgent,
  onAgentChatSend,
  onCustomizeUseCase,
}: LandingProps) {
  const c = copy(theme.tone);
  const [matches, setMatches] = useState<CatalogItem[]>([]);
  const [betaTokenInput, setBetaTokenInput] = useState("");
  const q = input.trim().toLowerCase();
  const mainInputRef = useRef<HTMLInputElement>(null);

  // ── starter gallery (Iteration 4 — user-value loop) ───────────────────────
  const [galleryItems, setGalleryItems] = useState<GalleryListItem[]>([]);
  const [authNudgeSlug, setAuthNudgeSlug] = useState<string | undefined>();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [detail, setDetail] = useState<GalleryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | undefined>();
  const [bundleExpanded, setBundleExpanded] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadError, setDownloadError] = useState<string | undefined>();
  // "Copy as prompt" (Iteration 5 — one-click export) — the gallery detail
  // row already has the full bundle_md in hand (galleryDetail fetched it),
  // so no extra request is needed here, unlike DeliverCard's useChat path.
  const [copyPromptCopied, setCopyPromptCopied] = useState(false);
  const copyPromptTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // GET /api/gallery is open (no auth) — fetch once on mount regardless of
  // the beta gate, so the card grid is visible pre-login.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const r = await galleryList();
      if (cancelled || isApiError(r)) return;
      setGalleryItems(r);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // "Copy as prompt" 2s flip-back timer cleanup on unmount.
  useEffect(() => {
    return () => {
      if (copyPromptTimerRef.current) clearTimeout(copyPromptTimerRef.current);
    };
  }, []);

  const closeDetail = () => {
    setSelectedSlug(null);
    setDetail(null);
    setDetailError(undefined);
    setChatOpen(false);
    setBundleExpanded(false);
    setDownloadBusy(false);
    setDownloadError(undefined);
    setCopyPromptCopied(false);
  };

  const loadDetail = async (slug: string) => {
    setSelectedSlug(slug);
    setDetail(null);
    setDetailError(undefined);
    setChatOpen(false);
    setBundleExpanded(false);
    setDownloadError(undefined);
    setCopyPromptCopied(false);
    setDetailLoading(true);
    const r = await galleryDetail(slug, adminToken ?? "");
    setDetailLoading(false);
    if (isApiError(r)) {
      setDetailError(
        r.status === 401
          ? "Enter your beta access token above to view this agent."
          : "Could not load this agent — try again."
      );
      return;
    }
    setDetail(r);
  };

  // Clicking a card when not authenticated prompts the beta gate first
  // (already visible above, in place of the task input) instead of trying
  // (and 401-ing) the detail fetch.
  const handleCardClick = (slug: string) => {
    if (gate) {
      setAuthNudgeSlug(slug);
      return;
    }
    setAuthNudgeSlug(undefined);
    void loadDetail(slug);
  };

  const handleDownload = async () => {
    if (!detail || downloadBusy) return;
    setDownloadBusy(true);
    setDownloadError(undefined);
    const r = await skillBundlesZip(detail.team_id, adminToken ?? "");
    setDownloadBusy(false);
    if (isApiError(r)) {
      setDownloadError("Download failed — check your access token and try again.");
      return;
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(r);
    a.download = `${detail.slug}-agent-skills.zip`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const handleCustomize = () => {
    if (!detail) return;
    onCustomizeUseCase?.(detail.use_case);
    closeDetail();
    mainInputRef.current?.focus();
  };

  // "Copy as prompt" (Iteration 5 — one-click export) — same clipboard
  // content as DeliverCard's onCopyPrompt (preamble + full SKILL.md), but
  // synchronous here since detail.bundle_md is already loaded.
  const handleCopyPrompt = async () => {
    if (!detail) return;
    try {
      await navigator.clipboard.writeText(COPY_AS_PROMPT_PREAMBLE + detail.bundle_md);
    } catch {
      /* clipboard may be unavailable */
    }
    setCopyPromptCopied(true);
    if (copyPromptTimerRef.current) clearTimeout(copyPromptTimerRef.current);
    copyPromptTimerRef.current = setTimeout(() => setCopyPromptCopied(false), 2000);
  };

  const bundleLines = detail?.bundle_md.split("\n") ?? [];
  const bundlePreviewLines = bundleExpanded ? bundleLines : bundleLines.slice(0, 30);
  const bundleTruncated = !bundleExpanded && bundleLines.length > 30;
  const chatKey = detail ? `${detail.team_id}:${detail.agent_id}` : undefined;

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
        overflowY: "auto",
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
                ref={mainInputRef}
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

      {/* Starter gallery (Iteration 4 — user-value loop). Renders regardless
          of `gate` — GET /api/gallery is open — so it's visible pre-login;
          opening a card is what prompts the beta gate. */}
      {galleryItems.length > 0 && (
        <div style={{ width: "100%", maxWidth: 660, marginTop: 34 }}>
          <div
            style={{
              fontSize: 11.5,
              color: C.dim,
              textAlign: "center",
              marginBottom: 10,
            }}
          >
            Start from a proven agent
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center" }}>
            {galleryItems.map((item) => (
              <div key={item.slug} style={{ display: "flex", flexDirection: "column" }}>
                <button style={GALLERY_CARD} onClick={() => handleCardClick(item.slug)}>
                  <div style={{ fontWeight: 650, fontSize: 13.5 }}>{item.label}</div>
                  <div style={{ fontSize: 12, color: C.muted, marginTop: 4, lineHeight: 1.4 }}>
                    {item.blurb}
                  </div>
                </button>
                {gate && authNudgeSlug === item.slug && (
                  <div style={{ fontSize: 11, color: C.gap, marginTop: 6, maxWidth: 208 }}>
                    Enter your beta access token above to try this agent.
                  </div>
                )}
              </div>
            ))}
          </div>

          {selectedSlug && (
            <div style={DETAIL_PANEL}>
              {detailLoading && (
                <div style={{ fontSize: 12.5, color: C.dim }}>Loading…</div>
              )}
              {detailError && (
                <div style={{ fontSize: 12.5, color: C.gap }}>{detailError}</div>
              )}
              {detail && (
                <>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      gap: 10,
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 650, fontSize: 15 }}>{detail.label}</div>
                      <div style={{ fontSize: 12.5, color: C.muted, marginTop: 4 }}>
                        {detail.blurb}
                      </div>
                    </div>
                    <button
                      onClick={closeDetail}
                      aria-label="Close"
                      style={{
                        border: "none",
                        background: "transparent",
                        color: C.faint,
                        fontSize: 15,
                        padding: "2px 6px",
                        cursor: "pointer",
                        flex: "none",
                      }}
                    >
                      ✕
                    </button>
                  </div>

                  <div style={{ marginTop: 12 }}>
                    <button
                      onClick={() => setBundleExpanded((v) => !v)}
                      style={{
                        border: "none",
                        background: "transparent",
                        padding: 0,
                        fontSize: 12,
                        fontWeight: 600,
                        color: C.muted,
                        cursor: "pointer",
                      }}
                    >
                      {bundleExpanded ? "▾" : "▸"} Agent spec preview
                    </button>
                    <pre style={BUNDLE_PRE}>
                      {bundlePreviewLines.join("\n")}
                      {bundleTruncated ? "\n…" : ""}
                    </pre>
                  </div>

                  <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                    <button
                      onClick={() => setChatOpen((v) => !v)}
                      style={{
                        border: "none",
                        background: theme.accent,
                        color: "#fff",
                        borderRadius: 10,
                        padding: "9px 16px",
                        fontSize: 13,
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      {chatOpen ? "▾" : "▸"} Try this agent
                    </button>
                    <button onClick={handleDownload} disabled={downloadBusy} style={secondaryBtn}>
                      {downloadBusy ? "⏳ Preparing…" : "⬇ Download"}
                    </button>
                    <button onClick={handleCopyPrompt} style={secondaryBtn}>
                      {copyPromptCopied ? "✓ Copied" : "📋 Copy as prompt"}
                    </button>
                    <InstallPopover accent={theme.accent} />
                    <button onClick={handleCustomize} style={secondaryBtn}>
                      ✎ Customize for my business
                    </button>
                  </div>
                  {downloadError && (
                    <div style={{ fontSize: 12, color: C.gap, marginTop: 8 }}>{downloadError}</div>
                  )}

                  <TryAgentChat
                    roleName={detail.label}
                    useCase={detail.use_case}
                    accent={theme.accent}
                    chat={chatKey ? chatByAgent?.[chatKey] : undefined}
                    onSend={(text) => onAgentChatSend?.(detail.team_id, detail.agent_id, text)}
                    expanded={chatOpen}
                  />
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
