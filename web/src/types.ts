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

// ── Real-inputs intake (Iteration 3 — user-value loop) ──────────────────────
// PUT /api/team/{team_id}/inputs {user_inputs: UserInputItem[]}. Baked
// verbatim into the generated SKILL.md (app.services.skill_bundle_service),
// so a saved input flows automatically into "try your agent" chat.
export interface UserInputItem {
  kind: string;
  name: string;
  content: string;
}
export interface UserInputsResp {
  ok: boolean;
  count: number;
  bytes: number;
}
// Local save-flow state for the TeamCard's "paste it now" panel, keyed by
// teamId in ChatState.userInputsByTeam (mirrors feedbackByTeam/chatByAgent).
export interface UserInputsSaveState {
  saving: boolean;
  saved?: { count: number; bytes: number };
  error?: string;
  // Last full list actually PUT to the server (PUT /inputs REPLACES, so this
  // is the client's record of "what's saved now" — the ops-questions card
  // (Iteration 2) reads it to merge its own answers in rather than clobbering
  // whatever the "paste it now" panel already saved, and vice versa).
  items?: UserInputItem[];
}

// ── Ops-question interview (Iteration 2 — "make it yours" UI) ───────────────
// POST /api/team/{team_id}/ops-questions (no body) — fired once right after a
// team is revealed. At most settings.OPS_QUESTIONS_MAX questions, each tagged
// with a kind from app.llm_contracts.OPS_QUESTION_KINDS (also a valid
// UserInputItem.kind, so an answer PUTs straight back to /inputs unchanged).
export interface OpsQuestion {
  kind: string;
  name: string;
  question: string;
}
export interface OpsQuestionsResp {
  questions: OpsQuestion[];
}
// Local state for the OpsQuestionsCard, keyed by teamId in
// ChatState.opsQuestionsByTeam. `answers` is keyed by the question's index
// in `questions` (stable for the lifetime of one card — there's only ever
// one round). Absence from the map (vs. an entry with an empty `questions`
// array) is what "haven't fetched yet" looks like; either way zero questions
// or `dismissed: true` renders nothing.
export interface OpsQuestionsState {
  questions: OpsQuestion[];
  answers: Record<number, string>;
  saved: boolean;
  savedCount?: number;
  dismissed: boolean;
  busy: boolean;
  error?: string;
  // Iteration 4 (convergence + polish) — true once a post-save re-fetch of
  // /ops-questions comes back empty AND at least one input has ever been
  // saved for this team (userInputsByTeam[teamId].saved is the source of
  // truth for "ever saved"). Distinguishes real convergence ("nothing more
  // to ask") from the ordinary empty-on-first-load case (no card at all —
  // see loadOpsQuestions), which never sets this flag.
  done?: boolean;
}

// ── Transcript extraction (Iteration 3 — OPERATIONS-INTAKE loop) ────────────
// POST /api/team/{team_id}/transcript {text}. Returns a PROPOSAL only —
// nothing is stored server-side by this call; the same {kind, name, question}
// shape as OpsQuestion is reused for open_questions so they merge straight
// into the same answer flow. `content` in items maps 1:1 onto UserInputItem.
export interface TranscriptExtractItem {
  kind: string;
  name: string;
  content: string;
}
export interface TranscriptExtractOpenQuestion {
  kind: string;
  name: string;
  question: string;
}
export interface TranscriptExtractResp {
  items: TranscriptExtractItem[];
  open_questions: TranscriptExtractOpenQuestion[];
}
// Local UI state for the OpsQuestionsCard's transcript panel, keyed by teamId
// in ChatState.transcriptByTeam. `itemDrafts`/`removedItems` are keyed by the
// index into `proposal.items` (stable for the lifetime of one proposal).
export interface TranscriptState {
  open: boolean;
  text: string;
  busy: boolean;
  error?: string;
  proposal?: TranscriptExtractResp;
  itemDrafts: Record<number, string>;
  removedItems: Record<number, boolean>;
}

// ── Starter gallery (Iteration 4 — user-value loop) ─────────────────────────
// GET /api/gallery (open, no auth) — metadata only, so the gallery renders on
// Landing before the beta gate. GET /api/gallery/{slug} (beta-gated) — the
// full row, including the composed bundle_md and the team_id/agent_id used
// to wire "Try this agent" (POST .../chat) and "Download" (POST
// .../skill-bundles) against the existing team endpoints.
//
// Iteration 6 (battery top-20 + gallery receipts): both endpoints add a
// receipts summary, read from verify_runs by app.services.verify_service,
// ONLY when an admin has actually run verify for that agent — proven/
// exec_skills (list) and the full GalleryReceipts (detail) are absent
// otherwise. Never render a badge/strip when absent; never fake one.
export interface GalleryListItem {
  slug: string;
  label: string;
  blurb: string;
  proven?: boolean;
  exec_skills?: number;
}
export interface GalleryReceipts {
  proven: boolean;
  exec_skills: number;
  exec_avg_pct: number | null;
  rubric_pct: number | null;
  verified_at: string;
}
export interface GalleryDetail extends GalleryListItem {
  use_case: string;
  team_id: string;
  agent_id: string;
  bundle_md: string;
  receipts?: GalleryReceipts;
}

// ── Skill-bundle JSON export (Iteration 5 — one-click export) ──────────────
// POST /api/team/{tid}/skill-bundles {format: "json"} — same compose as the
// zip export, but returns every agent's SKILL.md inline so the frontend can
// pick one out (e.g. for "Copy as prompt") without downloading a file.
export interface SkillBundleItem {
  agent_id: string;
  role: string;
  skill_md: string;
  filename?: string;
}
export interface SkillBundlesResp {
  team_id: string;
  use_case: string;
  coherence?: unknown;
  bundles: SkillBundleItem[];
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
