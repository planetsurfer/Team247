// Thin fetch wrappers for the FastAPI backend + admin-token storage + job poller.
// Mirrors the existing api()/pollJob patterns in the legacy demo.html (lines 747,
// 1922-1957) so behavior is consistent across the two frontends.

import type {
  CatalogResp,
  CardPayload,
  ChatTurnMessage,
  GalleryDetail,
  GalleryListItem,
  JobStatus,
  OpsQuestionsResp,
  RecommendResp,
  SkillBundlesResp,
  TeamSkills,
  UserInputItem,
  UserInputsResp,
  VerifyResult,
} from "./types";

// ── admin token (localStorage — localhost single-user, matches demo.html) ──
const TOKEN_KEY = "team247.adminToken";

export function getAdminToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}
export function setAdminToken(t: string): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

// ── beta-access token (closed beta — PRODUCTION_ROADMAP.md P0 #1) ──────────
// Separate from the admin token: this is the per-user token minted by
// `python -m app.mint_token`, gating every LLM-driving endpoint when the
// server has BETA_AUTH on. Attached to every /api request by `api()` /
// `apiVerify()` below.
const BETA_TOKEN_KEY = "team247.betaToken";

export function getBetaToken(): string {
  return localStorage.getItem(BETA_TOKEN_KEY) ?? "";
}
export function setBetaToken(t: string): void {
  if (t) localStorage.setItem(BETA_TOKEN_KEY, t);
  else localStorage.removeItem(BETA_TOKEN_KEY);
}

// On first mount: a one-shot ?token= share param seeds the token, then is
// stripped from the URL so it doesn't linger in history.
export function seedTokenFromUrl(): string {
  const existing = getAdminToken();
  if (existing) return existing;
  try {
    const u = new URL(window.location.href);
    const t = u.searchParams.get("token");
    if (t) {
      setAdminToken(t);
      u.searchParams.delete("token");
      window.history.replaceState({}, "", u.toString());
      return t;
    }
  } catch {
    /* ignore — URL parsing is best-effort */
  }
  return "";
}

// ── fetch wrappers (no-throw — returns {error} on failure, like demo.html) ──
export interface ApiError {
  error: string;
  status?: number;
  retryAfter?: number;
  detail?: unknown;
}

async function req<T>(path: string, opts?: RequestInit): Promise<T | ApiError> {
  try {
    const r = await fetch(path, opts ?? {});
    const text = await r.text();
    let body: unknown = undefined;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!r.ok) {
      const retryAfter = r.headers.get("retry-after");
      return {
        error: String(r.status),
        status: r.status,
        retryAfter: retryAfter ? Number(retryAfter) : undefined,
        detail: body,
      };
    }
    return body as T;
  } catch (e) {
    return { error: "network", detail: String(e) };
  }
}

// JSON GET/POST to /api/<path>. Attaches Authorization: Bearer <betaToken>
// when one is stored (closed beta — PRODUCTION_ROADMAP.md P0 #1); a caller
// that already set its own Authorization header (e.g. apiVerify) wins.
export function api<T>(path: string, opts?: RequestInit): Promise<T | ApiError> {
  const headers: Record<string, string> = {
    ...(opts?.headers as Record<string, string> | undefined),
  };
  const betaToken = getBetaToken();
  if (betaToken && !headers["Authorization"]) headers["Authorization"] = "Bearer " + betaToken;
  return req<T>("/api" + path, { ...opts, headers });
}

// Drop-in agent bundle: beta-gated POST /team/{id}/skill-bundles?format=zip
// (admin token also accepted — it's a strict superset). Returns the zip Blob
// (one <role-slug>/SKILL.md per agent — loadable into Claude/Codex or any
// Agent Skills harness) or ApiError (401 -> re-prompt token). `token` (admin)
// wins when set; otherwise falls back to the stored beta token.
// use_case is omitted on purpose: the server falls back to the team's stored one.
export async function skillBundlesZip(
  teamId: string,
  token: string
): Promise<Blob | ApiError> {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const effective = token || getBetaToken();
    if (effective) headers["Authorization"] = "Bearer " + effective;
    const r = await fetch(`/api/team/${teamId}/skill-bundles`, {
      method: "POST",
      headers,
      body: JSON.stringify({ format: "zip" }),
    });
    if (!r.ok) {
      return { error: String(r.status), status: r.status, detail: await r.text() };
    }
    return await r.blob();
  } catch (e) {
    return { error: "network", detail: String(e) };
  }
}

// Iteration 5 (one-click export) — same POST as skillBundlesZip but with the
// server's default `format: "json"` instead of "zip": returns every agent's
// composed SKILL.md inline (bundles[].skill_md) rather than a zip Blob, so
// "Copy as prompt" can grab the selected agent's markdown without a download
// round-trip. Same beta-gated, admin-token-wins-else-beta-token auth as
// skillBundlesZip/putTeamInputs (apiVerify).
export const skillBundleMd = (teamId: string, adminToken: string) =>
  apiVerify<SkillBundlesResp>(`/team/${teamId}/skill-bundles`, adminToken, {
    method: "POST",
    body: JSON.stringify({ format: "json" }),
  });

// Plaintext GET (for /specs/{agent_id} which is PlainTextResponse).
export async function apiText(path: string, opts?: RequestInit): Promise<string | ApiError> {
  const headers: Record<string, string> = {
    ...(opts?.headers as Record<string, string> | undefined),
  };
  const betaToken = getBetaToken();
  if (betaToken && !headers["Authorization"]) headers["Authorization"] = "Bearer " + betaToken;
  try {
    const r = await fetch("/api" + path, { ...opts, headers });
    const text = await r.text();
    if (!r.ok) return { error: String(r.status), status: r.status, detail: text };
    return text;
  } catch (e) {
    return { error: "network", detail: String(e) };
  }
}

// Admin-gated call — injects Authorization: Bearer <token>. `token` (admin)
// wins when set; otherwise falls back to the stored beta token, so admin-only
// endpoints (verify) still correctly 401 for a beta-only caller while
// beta-relaxed endpoints called through this helper still authenticate.
// Returns ApiError with status=401 / 429 distinctly so the UI can
// clear/re-prompt the token.
export async function apiVerify<T>(
  path: string,
  token: string,
  opts?: RequestInit
): Promise<T | ApiError> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts?.headers as Record<string, string> | undefined),
  };
  const effective = token || getBetaToken();
  if (effective) headers["Authorization"] = "Bearer " + effective;
  return req<T>("/api" + path, { ...opts, headers });
}

export function isApiError<T>(v: T | ApiError): v is ApiError {
  return (
    v !== null &&
    typeof v === "object" &&
    "error" in (v as object) &&
    typeof (v as ApiError).error === "string"
  );
}

// ── beta-access status (GET /api/auth/status — open, no LLM) ───────────────
export interface AuthStatus {
  beta_auth: boolean;
  authenticated: boolean;
}

// Checks a specific token (defaults to the stored one) against the server
// without persisting it — the caller decides whether/when to store it via
// setBetaToken(). Used both by the startup gate check and by the Landing
// beta-gate form's submit handler.
export const authStatus = (token?: string) => {
  const headers: Record<string, string> = {};
  const t = token ?? getBetaToken();
  if (t) headers["Authorization"] = "Bearer " + t;
  return req<AuthStatus>("/api/auth/status", { headers });
};

// ── typed endpoint helpers ──────────────────────────────────────────────────
export const catalogSearch = (q: string, size = 10) =>
  api<CatalogResp>(`/catalog?q=${encodeURIComponent(q)}&size=${size}`);

export const catalogCard = (roleId: number) => api<CardPayload>(`/catalog/${roleId}/card`);

// ── Starter gallery (Iteration 4 — user-value loop) ─────────────────────────
// GET /api/gallery is OPEN (no auth) — deliberately using the plain `api()`
// helper (never fails on a missing beta token) so the gallery renders on
// Landing before the beta gate. GET /api/gallery/{slug} is beta-gated, so it
// goes through `apiVerify`'s "adminToken wins, else stored beta token"
// fallback like every other beta-gated call in this file.
export const galleryList = () => api<GalleryListItem[]>(`/gallery`);

export const galleryDetail = (slug: string, adminToken: string) =>
  apiVerify<GalleryDetail>(`/gallery/${slug}`, adminToken);

export const recommend = (useCase: string) =>
  api<RecommendResp>(`/team/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ use_case: useCase }),
  });

// Async variant (iteration 4 — async recommend, mirrors renderAsync/verifyAsync):
// submits the same body but returns a job_id to poll instead of blocking the
// request on the LLM call. Sync-path `recommend` above is unchanged/untouched
// (API compat: tests/eval harnesses still use it).
export const recommendAsync = (useCase: string) =>
  api<{ job_id: string; poll: string; async: boolean }>(
    `/team/recommend?async_mode=true`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_case: useCase }),
    }
  );

export const teamSkills = (teamId: string, agentId: string) =>
  api<TeamSkills>(`/team/${teamId}/skills/${agentId}`);

export const putAgent = (
  teamId: string,
  agentId: string,
  body: { skill_overrides?: Record<string, number>; skill_disabled?: string[] }
) =>
  api<{ agent_id: string }>(`/team/${teamId}/agents/${agentId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

// Iteration 3 (real-inputs intake) — PUT /api/team/{tid}/inputs. Beta-gated
// but never LLM-driven (pure validate+store), so this goes through
// apiVerify's "adminToken wins, else stored beta token" fallback just like
// submitFeedback/agentChat above, keeping it usable for plain beta testers.
// A 422 (validation) or 404 (unknown team) surfaces via ApiError as usual.
export const putTeamInputs = (
  teamId: string,
  userInputs: UserInputItem[],
  adminToken: string
) =>
  apiVerify<UserInputsResp>(`/team/${teamId}/inputs`, adminToken, {
    method: "PUT",
    body: JSON.stringify({ user_inputs: userInputs }),
  });

// Iteration 2 ("make it yours" UI) — POST /api/team/{tid}/ops-questions (no
// body). Beta-gated but not admin-only (same convention as renderAsync): the
// plain api() helper already attaches the stored beta token, so no adminToken
// param is needed here. Fired once right after a team is revealed; returns
// 0..OPS_QUESTIONS_MAX questions (0 is valid — nothing left worth asking).
export const opsQuestions = (teamId: string) =>
  api<OpsQuestionsResp>(`/team/${teamId}/ops-questions`, { method: "POST" });

export const renderAsync = (teamId: string) =>
  api<{ job_id: string; poll: string; async: boolean }>(
    `/team/${teamId}/render?async_mode=true`,
    { method: "POST", headers: { "Content-Type": "application/json" } }
  );

export const verifyAsync = (teamId: string, agentId: string, token: string) =>
  apiVerify<{ job_id: string; poll: string; async: boolean } | { status: unknown }>(
    `/team/${teamId}/agents/${agentId}/verify?async_mode=true`,
    token,
    { method: "POST" }
  );

export const specMd = (teamId: string, agentId: string) =>
  apiText(`/team/${teamId}/specs/${agentId}`);

export const jobStatus = (jid: string) => api<JobStatus>(`/jobs/${jid}`);

// Iteration 1 (beta feedback instrumentation) — POST /api/feedback. Beta-gated
// but not admin-only, so it goes through apiVerify's same "adminToken wins,
// else stored beta token" fallback (works for admin users AND plain beta
// testers) rather than the plain api() helper's beta-token-only lookup.
// Idempotent server-side: a second call for the same team upserts the same
// row instead of duplicating (see app/routers/feedback.py).
export const submitFeedback = (
  teamId: string,
  verdict: "up" | "down",
  comment: string | undefined,
  adminToken: string
) =>
  apiVerify<{ team_id: string; verdict: string; ok: boolean }>(`/feedback`, adminToken, {
    method: "POST",
    body: JSON.stringify({ team_id: teamId, verdict, comment }),
  });

// Iteration 2 (try-your-agent chat) — POST /api/team/{tid}/agents/{aid}/chat.
// Beta-gated (same convention as verify/skill-bundles): apiVerify's
// "adminToken wins, else stored beta token" fallback. `messages` is the
// caller's already-trimmed (<=20) turn history, last entry a user turn.
// 429 (daily chat allowance) surfaces via ApiError.status for the UI to
// show the friendly "try again tomorrow" copy.
export const agentChat = (
  teamId: string,
  agentId: string,
  messages: ChatTurnMessage[],
  adminToken: string
) =>
  apiVerify<{ reply: string }>(`/team/${teamId}/agents/${agentId}/chat`, adminToken, {
    method: "POST",
    body: JSON.stringify({ messages }),
  });

// ── poller ──────────────────────────────────────────────────────────────────
export interface PollHandle {
  stop: () => void;
}

// Poll /api/jobs/{jid} every `intervalMs` until terminal. onDone/onFail fire
// with the final JobStatus; onPoll fires on each non-terminal tick. Cancellable
// via the returned stop() — used when a prove card unmounts mid-run.
export function pollJob(
  jid: string,
  handlers: {
    onPoll?: (s: JobStatus) => void;
    onDone?: (s: JobStatus) => void;
    onFail?: (s: JobStatus) => void;
  },
  intervalMs = 3000
): PollHandle {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = async () => {
    if (stopped) return;
    const s = await jobStatus(jid);
    if (stopped) return;
    if (isApiError(s)) {
      // network blip — keep trying rather than killing the run silently
      timer = setTimeout(tick, intervalMs);
      return;
    }
    if (s.status === "done") {
      handlers.onDone?.(s);
      return; // stop
    }
    if (s.status === "failed" || s.status === "unknown") {
      handlers.onFail?.(s);
      return;
    }
    handlers.onPoll?.(s);
    timer = setTimeout(tick, intervalMs);
  };

  // immediate first check, then interval
  void tick();
  return {
    stop: () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    },
  };
}

// Cast helper — job `result` is untyped on the wire; narrow to VerifyResult.
export function asVerifyResult(v: unknown): VerifyResult | null {
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if (Array.isArray(o.exec_results) || Array.isArray(o.rubric_results)) {
    return v as VerifyResult;
  }
  return null;
}

// Cast helper — job `result` is untyped on the wire; narrow to RecommendResp
// (async /api/team/recommend and /api/intake/{sid}/recommend both land here).
export function asRecommendResult(v: unknown): RecommendResp | null {
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if (typeof o.team_id === "string" && Array.isArray(o.agents)) {
    return v as RecommendResp;
  }
  return null;
}
