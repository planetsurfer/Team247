// Backend data shapes + local UI state types.
// Mirrors the verified API contracts (see plan §Data-shape mapping).

// ── Catalog (GET /api/catalog, GET /api/catalog/{role_id}/card) ─────────────
export interface CatalogItem {
  role_id: number;
  role: string;
  sector: string;
  track: string;
  n_skills: number;
  n_executable: number;
}
export interface CatalogResp {
  items: CatalogItem[];
  total: number;
  page: number;
  size: number;
}

// Per-skill K&A row from teamspec.skill_rows (card + team skills endpoints).
// NOTE: the backend uses `nm`/`lvl`/`exec`, NOT `skill`/`required_level`.
export interface SkillRow {
  nm: string;
  code: string;
  lvl: number; // official required level (0..6)
  exec: boolean; // executable track (✔) vs rubric (○)
  prof?: string;
  ka?: unknown;
  guidance?: string;
}
export interface CardPayload {
  role_id: number;
  role: string;
  sector: string;
  track: string;
  n_skills: number;
  n_executable: number;
  sk: SkillRow[];
  [k: string]: unknown;
}

// ── Team recommend (POST /api/team/recommend {use_case}) ────────────────────
export interface Artifact {
  kind: "sample" | "blank_format" | "past_documents" | "database" | "none" | string;
  description: string;
}
export interface AgentOut {
  agent_id: string;
  role_id: number;
  role: string;
  stage: number;
  squad?: string;
  skill_level_overrides: Record<string, number>;
  rationale?: string;
  confidence?: number | null; // 0..100 — surfaced via the confidence passthrough
  matched_on?: string[] | null;
}
export interface Candidate {
  n: number;
  role: string;
  sector?: string;
  confidence?: number | null;
  matched_on?: string[] | null;
}
export interface RecommendResp {
  team_id: string;
  agents: AgentOut[];
  artifacts_needed?: Artifact[];
  recommendation_raw: { team: unknown[]; candidates: Candidate[] };
}

// ── Team skills (GET /api/team/{tid}/skills/{aid}) ──────────────────────────
export interface TeamSkills {
  agent_id: string;
  role: string;
  sk: SkillRow[];
}

// ── Two-track verify result (POST .../verify?async_mode=true → /api/jobs/{jid}) ─
export interface ExecResult {
  skill: string;
  code?: string;
  required_level: number;
  score: number; // 0..1
  held_level: number;
  gap?: number;
  sandbox_id?: string;
  error?: string;
  execution_verified: true;
}
export interface RubricResult {
  skill: string;
  level: number;
  covered: number;
  total: number;
  pct: number; // 0..100
  execution_verified: false;
}
export interface VerifyResult {
  verify_run_id?: string;
  exec_results: ExecResult[];
  rubric_results: RubricResult[];
  coverage_pct?: number | null; // exec-only, never blended (verify_service.py:98-105)
  two_track_note?: string;
  error?: string;
}

// ── Background jobs (GET /api/jobs/{jid}) ──────────────────────────────────
export type JobStatusName = "pending" | "running" | "done" | "failed" | "unknown";
export interface JobStatus {
  status: JobStatusName;
  kind?: string;
  result?: VerifyResult | unknown;
  error?: string | null;
  started_at?: string;
  finished_at?: string;
}

// ── Local UI state ──────────────────────────────────────────────────────────
// One role recommendation row in the team card.
export interface RoleRow {
  name: string;
  sector: string;
  conf: number | null; // percent; null if backend couldn't supply it
  matched: string;
  sel: boolean;
  role_id?: number;
  agent_id?: string;
}

// One skill row in the loadout / prove / deliver cards.
export interface SkillState {
  name: string;
  code: string;
  off: number; // official level
  target: number; // user-adjusted target (0..6)
  exec: boolean;
  base?: number; // baseline % (from verify exec score or rubric pct)
  fin?: number; // refined % (final verify score/pct)
  // verify-track identity for matching prove bars back to the row
  track: "exec" | "rubric";
}

// Discriminated union of chat thread messages — mirrors the prototype `kind` tags.
export type Message =
  | { kind: "user"; text: string }
  | { kind: "team" }
  | { kind: "loadout" }
  | { kind: "prove" }
  | { kind: "deliver" };

// Per-team beta feedback row state (Iteration 1 — user-value loop), keyed by
// teamId in ChatState.feedbackByTeam so re-rendering the same team's
// DeliverCard never re-asks. Undefined = not yet asked (show the ask row).
export interface FeedbackState {
  verdict?: "up" | "down"; // set once the caller picks a thumb
  comment?: string; // draft/submitted one-line comment
  commentSubmitted?: boolean; // true once the comment POST has landed
  dismissed?: boolean; // the row was closed (✕) without giving a verdict
}

// ── Try-your-agent chat (Iteration 2 — user-value loop) ─────────────────────
// POST /api/team/{tid}/agents/{aid}/chat {messages}. One thread per
// (teamId, agentId), keyed in ChatState.chatByAgent by `${teamId}:${agentId}`.
export interface ChatTurnMessage {
  role: "user" | "assistant";
  content: string;
}
export interface ChatThreadState {
  messages: ChatTurnMessage[];
  busy: boolean;
  error?: string;
}

export interface ProveState {
  jobId?: string;
  status: JobStatusName;
  result?: VerifyResult | null;
  t: number; // client-side animation clock (ms)
  done: boolean;
  error?: string | null;
}
