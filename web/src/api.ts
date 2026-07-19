// Thin fetch wrappers for the FastAPI backend + admin-token storage + job poller.
// Mirrors the existing api()/pollJob patterns in the legacy demo.html (lines 747,
// 1922-1957) so behavior is consistent across the two frontends.

import type {
  CatalogResp,
  CardPayload,
  JobStatus,
  RecommendResp,
  TeamSkills,
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

// JSON GET/POST to /api/<path>. Returns parsed JSON or {error}.
export function api<T>(path: string, opts?: RequestInit): Promise<T | ApiError> {
  return req<T>("/api" + path, opts);
}

// Plaintext GET (for /specs/{agent_id} which is PlainTextResponse).
export async function apiText(path: string, opts?: RequestInit): Promise<string | ApiError> {
  try {
    const r = await fetch("/api" + path, opts ?? {});
    const text = await r.text();
    if (!r.ok) return { error: String(r.status), status: r.status, detail: text };
    return text;
  } catch (e) {
    return { error: "network", detail: String(e) };
  }
}

// Admin-gated call — injects Authorization: Bearer <token>. Returns ApiError
// with status=401 / 429 distinctly so the UI can clear/re-prompt the token.
export async function apiVerify<T>(
  path: string,
  token: string,
  opts?: RequestInit
): Promise<T | ApiError> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts?.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = "Bearer " + token;
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

// ── typed endpoint helpers ──────────────────────────────────────────────────
export const catalogSearch = (q: string, size = 10) =>
  api<CatalogResp>(`/catalog?q=${encodeURIComponent(q)}&size=${size}`);

export const catalogCard = (roleId: number) => api<CardPayload>(`/catalog/${roleId}/card`);

export const recommend = (useCase: string) =>
  api<RecommendResp>(`/team/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ use_case: useCase }),
  });

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
