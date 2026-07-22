// useChat — the single state machine for the Team247 chat flow.
//
// Ports the prototype's `Component` class (Team247 Chat.dc.html) — its `state`,
// `renderVals()` view-model, and `send/pickRole/confirmTeam/setTargets/generate/
// download/copy` handlers — to a React hook, replacing every mock with a live
// FastAPI call (see plan §useChat mapping table). The prove animation is driven
// from the real two-track verify job (polled + interpolated client-side).

import { useCallback, useEffect, useRef, useState } from "react";
import {
  agentChat,
  asRecommendResult,
  asVerifyResult,
  authStatus,
  catalogCard,
  catalogSearch,
  extractTranscript,
  getAdminToken,
  isApiError,
  opsQuestions,
  pollJob,
  putAgent,
  putTeamInputs,
  recommendAsync,
  renderAsync,
  seedTokenFromUrl,
  setAdminToken as persistToken,
  setBetaToken as persistBetaToken,
  skillBundleMd,
  skillBundlesZip,
  specMd as fetchSpecMd,
  submitFeedback as apiSubmitFeedback,
  teamSkills,
  verifyAsync,
} from "./api";
import { COPY_AS_PROMPT_PREAMBLE } from "./theme";
import type {
  Artifact,
  ChatThreadState,
  FeedbackState,
  Message,
  OpsQuestion,
  OpsQuestionsState,
  ProveState,
  RoleRow,
  SkillState,
  TranscriptState,
  UserInputItem,
  UserInputsSaveState,
  VerifyResult,
} from "./types";

interface ChatState {
  messages: Message[];
  input: string;
  pending: boolean;
  running: boolean;
  prove: ProveState | null;
  roleName: string;
  roles: RoleRow[];
  skills: SkillState[];
  artifacts: Artifact[];
  copied: boolean;
  // backend refs
  teamId?: string;
  agentId?: string;
  renderJobId?: string;
  specMd?: string;
  specReady?: boolean;
  adminToken: string;
  tokenRejected?: boolean;
  bundleBusy?: boolean;   // drop-in agent zip is being generated server-side
  sendError?: string;     // async recommend job failed with a user-facing message
                           // (e.g. NoDatasetRoleMatch — "could not match…")
  // "Copy as prompt" (Iteration 5 — one-click export) — cached per delivered
  // agent (`${teamId}:${agentId}`) so repeat copies of the same agent are
  // instant instead of re-fetching the composed SKILL.md every click.
  skillMdByAgent: Record<string, string>;
  copyPromptBusy?: boolean;
  copyPromptCopied?: boolean;
  // beta feedback (Iteration 1 — user-value loop), keyed by teamId so the
  // DeliverCard's ask-row fires once per team even across re-renders.
  feedbackByTeam: Record<string, FeedbackState>;
  // try-your-agent chat (Iteration 2 — user-value loop), keyed by
  // `${teamId}:${agentId}` so history is independent per delivered agent.
  chatByAgent: Record<string, ChatThreadState>;
  // real-inputs intake (Iteration 3 — user-value loop) — TeamCard's "paste it
  // now" panel save flow, keyed by teamId.
  userInputsByTeam: Record<string, UserInputsSaveState>;
  // ops-question interview (Iteration 2 — "make it yours" UI), keyed by
  // teamId. Populated by a fire-and-forget fetch right after a team is
  // revealed; absent entirely if the fetch is still in flight, failed, or
  // came back with zero questions (OpsQuestionsCard renders nothing either way).
  opsQuestionsByTeam: Record<string, OpsQuestionsState>;
  // transcript extraction (Iteration 3 — OPERATIONS-INTAKE loop), keyed by
  // teamId. The OpsQuestionsCard's "paste a meeting transcript instead"
  // panel — populated on toggle, not eagerly like opsQuestionsByTeam.
  transcriptByTeam: Record<string, TranscriptState>;
  // beta-access gate (closed beta — PRODUCTION_ROADMAP.md P0 #1)
  betaChecked: boolean;        // has the initial /api/auth/status probe resolved?
  betaAuth: boolean;           // server has BETA_AUTH on
  betaAuthenticated: boolean;  // true when betaAuth is off, or a valid token is stored
  betaTokenError?: string;
}

const INITIAL: ChatState = {
  messages: [],
  input: "",
  pending: false,
  running: false,
  prove: null,
  roleName: "",
  roles: [],
  skills: [],
  artifacts: [],
  copied: false,
  adminToken: "",
  skillMdByAgent: {},
  feedbackByTeam: {},
  chatByAgent: {},
  userInputsByTeam: {},
  opsQuestionsByTeam: {},
  transcriptByTeam: {},
  betaChecked: false,
  betaAuth: false,
  betaAuthenticated: true,
};

export function useChat() {
  const [state, setState] = useState<ChatState>(INITIAL);
  // mutable refs for the animation loop + poller + debounce so they survive
  // re-renders without being in deps.
  const rafRef = useRef<number | null>(null);
  const pollRef = useRef<ReturnType<typeof pollJob> | null>(null);
  const renderPollRef = useRef<ReturnType<typeof pollJob> | null>(null);
  const sendPollRef = useRef<ReturnType<typeof pollJob> | null>(null);
  const proveStartRef = useRef<number>(0);
  const putTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copyPromptTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // seed admin token from localStorage / ?token= on mount
  useEffect(() => {
    const t = seedTokenFromUrl() || getAdminToken();
    if (t) setState((s) => ({ ...s, adminToken: t }));
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (pollRef.current) pollRef.current.stop();
      if (renderPollRef.current) renderPollRef.current.stop();
      if (sendPollRef.current) sendPollRef.current.stop();
      if (putTimerRef.current) clearTimeout(putTimerRef.current);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      if (copyPromptTimerRef.current) clearTimeout(copyPromptTimerRef.current);
    };
  }, []);

  // beta-access gate: probe /api/auth/status once on mount (uses whatever
  // beta token is already in localStorage, if any). If the server doesn't
  // require beta auth, or the stored token is already valid, the gate never
  // shows and Landing renders normally.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const r = await authStatus();
      if (cancelled) return;
      if (isApiError(r)) {
        // network blip — fail open on the check itself (don't strand the UI
        // in a perpetual loading state); the endpoints themselves still
        // enforce the real gate server-side.
        setState((s) => ({ ...s, betaChecked: true }));
        return;
      }
      setState((s) => ({
        ...s,
        betaAuth: r.beta_auth,
        betaAuthenticated: r.beta_auth ? r.authenticated : true,
        betaChecked: true,
      }));
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ── helpers ──────────────────────────────────────────────────────────────
  const push = useCallback(
    (msg: Message, extra?: Partial<ChatState>) => {
      setState((s) => ({ ...s, ...extra, messages: [...s.messages, msg] }));
    },
    []
  );

  const patch = useCallback((extra: Partial<ChatState>) => {
    setState((s) => ({ ...s, ...extra }));
  }, []);

  // ── ops-question interview (Iteration 2 — "make it yours" UI) ────────────
  // Fire-and-forget: fetched right after a team is revealed, in parallel with
  // whatever the user does next (loadout / confirm). A failed fetch or zero
  // questions just means no card ever appears for this team — never blocks
  // or errors the main flow.
  const loadOpsQuestions = useCallback(async (teamId: string) => {
    const r = await opsQuestions(teamId);
    if (isApiError(r) || !r.questions || r.questions.length === 0) return;
    setState((s) => ({
      ...s,
      opsQuestionsByTeam: {
        ...s.opsQuestionsByTeam,
        [teamId]: { questions: r.questions, answers: {}, saved: false, dismissed: false, busy: false },
      },
    }));
  }, []);

  // ── role search / pick ────────────────────────────────────────────────────
  const pickRole = useCallback(
    async (name: string) => {
      const clean = name.trim();
      if (!clean) return;
      push({ kind: "user", text: "Role: " + clean }, { input: "", pending: true });
      // find the role in the live catalog, then fetch its card to seed skills[]
      const search = await catalogSearch(clean, 10);
      if (isApiError(search)) {
        patch({ pending: false });
        return;
      }
      const lower = clean.toLowerCase();
      const hit =
        search.items.find((r) => r.role.toLowerCase() === lower) ||
        search.items.find((r) => r.role.toLowerCase().includes(lower)) ||
        search.items.find((r) => r.role.toLowerCase().startsWith(lower)) ||
        search.items[0];
      if (!hit) {
        // no catalog match — fall back to free-text recommend
        patch({ pending: false });
        void sendInternal(clean);
        return;
      }
      const card = await catalogCard(hit.role_id);
      patch({
        roleName: hit.role,
        skills: card && !isApiError(card)
          ? card.sk.map((sk) => ({
              name: sk.nm,
              code: sk.code,
              off: sk.lvl,
              target: sk.lvl,
              exec: sk.exec,
              track: sk.exec ? ("exec" as const) : ("rubric" as const),
            }))
          : [],
        pending: false,
      });
      push({ kind: "loadout" });
    },
    [push, patch]
  );

  // ── submit task → team recommend ─────────────────────────────────────────
  const send = useCallback(
    (text?: string) => {
      const t = (text ?? stateRef.current.input).trim();
      if (!t || stateRef.current.pending) return;
      void sendInternal(t);
    },
    []
  );

  // keep a ref to the latest state so handlers defined above (send) read
  // current values without re-creating closures on every render.
  const stateRef = useRef(state);
  stateRef.current = state;

  // Async (iteration 4 — long recommend calls survive proxy timeouts as a
  // background job, same pattern as render/verify): submit → poll every 3s →
  // apply the result exactly as the old synchronous call did. `pending`
  // (and the TypingDots it drives) stays true across the whole poll, not
  // just the submit.
  const sendInternal = useCallback(
    async (text: string) => {
      push({ kind: "user", text }, { input: "", pending: true, sendError: undefined });
      const r = await recommendAsync(text);
      if (isApiError(r) || !(r as { job_id?: string }).job_id) {
        patch({ pending: false });
        return;
      }
      const rj = r as { job_id: string };
      sendPollRef.current = pollJob(
        rj.job_id,
        {
          onDone: (st) => {
            const rec = asRecommendResult(st.result);
            if (!rec) {
              patch({
                pending: false,
                sendError: "recommend failed — no result returned",
              });
              return;
            }
            const sectorByRole = new Map(
              (rec.recommendation_raw?.candidates ?? []).map((c) => [c.role, c.sector ?? ""])
            );
            const roles: RoleRow[] = rec.agents.map((a, i) => ({
              name: a.role,
              sector: sectorByRole.get(a.role) ?? "",
              conf: a.confidence ?? null,
              matched: (a.matched_on ?? []).join(", "),
              sel: i === 0,
              role_id: a.role_id,
              agent_id: a.agent_id,
            }));
            patch({
              teamId: rec.team_id,
              roles,
              artifacts: rec.artifacts_needed ?? [],
              roleName: roles[0]?.name ?? stateRef.current.roleName,
              pending: false,
              sendError: undefined,
            });
            push({ kind: "team" });
            void loadOpsQuestions(rec.team_id);
          },
          onFail: (st) => {
            // existing error path: patch pending false. The one case worth
            // surfacing to the user is the guardrail 422 the sync path also
            // raises (classify.NoDatasetRoleMatch) — team_service.recommend_async
            // wraps it so the job's error carries this same "could not match"
            // text either way.
            const msg = st.error ?? "";
            patch({
              pending: false,
              sendError: msg.toLowerCase().includes("could not match") ? msg : undefined,
            });
          },
        },
        3000
      );
    },
    [push, patch, loadOpsQuestions]
  );

  // ── team card: toggle + confirm ──────────────────────────────────────────
  const toggleRole = useCallback((i: number) => {
    setState((s) => ({
      ...s,
      roles: s.roles.map((r, j) => (j === i ? { ...r, sel: !r.sel } : r)),
    }));
  }, []);

  const confirmTeam = useCallback(async () => {
    const s = stateRef.current;
    const sel = [...s.roles].reverse().find((r) => r.sel) ?? s.roles.find((r) => r.sel);
    if (!sel) return;
    patch({ roleName: sel.name, agentId: sel.agent_id, pending: true });
    // fetch authoritative seeded skills (overrides already applied by recommend)
    if (s.teamId && sel.agent_id) {
      const sk = await teamSkills(s.teamId, sel.agent_id);
      if (!isApiError(sk)) {
        patch({
          skills: sk.sk.map((row) => ({
            name: row.nm,
            code: row.code,
            off: row.lvl,
            target: row.lvl,
            exec: row.exec,
            track: row.exec ? ("exec" as const) : ("rubric" as const),
          })),
          pending: false,
        });
      } else {
        patch({ pending: false });
      }
    } else {
      patch({ pending: false });
    }
    push({ kind: "loadout" });
  }, [push, patch]);

  // ── loadout: target editing (debounced PUT) + presets ────────────────────
  const persistLoadout = useCallback(() => {
    const s = stateRef.current;
    if (!s.teamId || !s.agentId) return;
    const skill_overrides: Record<string, number> = {};
    const skill_disabled: string[] = [];
    for (const k of s.skills) {
      if (k.target === 0) skill_disabled.push(k.code);
      else skill_overrides[k.code] = k.target;
    }
    void putAgent(s.teamId, s.agentId, { skill_overrides, skill_disabled });
  }, []);

  const setTargets = useCallback(
    (fn: (k: SkillState, j: number) => number) => {
      setState((s) => ({
        ...s,
        skills: s.skills.map((k, j) => ({
          ...k,
          target: Math.max(0, Math.min(6, fn(k, j))),
        })),
      }));
      // debounce the PUT so rapid stepper clicks don't flood
      if (putTimerRef.current) clearTimeout(putTimerRef.current);
      putTimerRef.current = setTimeout(persistLoadout, 500);
    },
    [persistLoadout]
  );

  const dec = useCallback(
    (i: number) =>
      setTargets((k, j) => (j === i ? k.target - 1 : k.target)),
    [setTargets]
  );
  const inc = useCallback(
    (i: number) =>
      setTargets((k, j) => (j === i ? k.target + 1 : k.target)),
    [setTargets]
  );

  const onBaseline = useCallback(() => setTargets((k) => k.off), [setTargets]);
  const onSpecialize = useCallback(
    () =>
      setTargets((k) =>
        k.name === "Data Analytics"
          ? k.off + 2
          : k.name === "Data Visualisation"
            ? k.off + 1
            : k.off
      ),
    [setTargets]
  );
  const onMax = useCallback(() => setTargets(() => 6), [setTargets]);

  // ── generate → prove (render + verify jobs) ───────────────────────────────
  const generate = useCallback(async () => {
    const s = stateRef.current;
    if (s.running) return;
    if (!s.skills.some((k) => k.target > 0)) return;
    if (!s.teamId || !s.agentId) return;

    patch({
      running: true,
      prove: { status: "pending", t: 0, done: false, error: null },
    });
    push({ kind: "prove" });

    // kick off spec render in parallel (don't block prove on it)
    const r = await renderAsync(s.teamId);
    if (!isApiError(r) && (r as { job_id?: string }).job_id) {
      const rj = r as { job_id: string; poll: string };
      patch({ renderJobId: rj.job_id });
      // poll render only to know when spec md is available
      renderPollRef.current = pollJob(rj.job_id, {
        onDone: () => patch({ specReady: true }),
        onFail: () => patch({ specReady: false }),
      });
    }

    // verify (admin-gated) — the prove animation driver
    const v = await verifyAsync(s.teamId, s.agentId, s.adminToken);
    if (isApiError(v)) {
      const ae = v as { error: string; status?: number };
      const unauthorized = ae.status === 401;
      patch({
        prove: {
          status: "failed",
          t: 0,
          done: false,
          error: unauthorized
            ? "admin token required to verify (set one in the bar above)"
            : `verify failed (${ae.error})`,
        },
        running: false,
        tokenRejected: unauthorized,
      });
      if (unauthorized) {
        // clear the bad token so the AdminTokenBar reappears
        persistToken("");
        setState((st) => ({ ...st, adminToken: "" }));
      }
      return;
    }
    const vj = v as { job_id: string; poll: string };
    proveStartRef.current = performance.now();
    patch({ prove: { jobId: vj.job_id, status: "pending", t: 0, done: false, error: null } });

    // start the client-side animation clock (rAF, decoupled from the 3s poll)
    startProveClock();

    // poll the verify job
    pollRef.current = pollJob(vj.job_id, {
      onPoll: (st) => {
        setState((prev) => ({
          ...prev,
          prove: prev.prove
            ? { ...prev.prove, status: st.status }
            : prev.prove,
        }));
      },
      onDone: (st) => onProveDone(st.result),
      onFail: (st) =>
        patch({
          prove: {
            status: "failed",
            t: 0,
            done: false,
            error: st.error ?? "verify failed",
          },
          running: false,
        }),
    });
  }, [push, patch]);

  // rAF clock: advance prove.t by elapsed wall-clock while status is non-terminal.
  // Bars fill toward real baseline/refined values once a poll returns them; until
  // then they fill toward 0 (spec line animates, "(baseline)" label).
  const startProveClock = useCallback(() => {
    const stop = () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
    const loop = () => {
      const s = stateRef.current;
      if (!s.prove || s.prove.done || s.prove.status === "failed") {
        stop();
        return;
      }
      const t = performance.now() - proveStartRef.current;
      patch({ prove: { ...s.prove, t } });
      rafRef.current = requestAnimationFrame(loop);
    };
    stop();
    rafRef.current = requestAnimationFrame(loop);
  }, [patch]);

  // On verify done: snap baselines to real values, tween rise to refined,
  // then push the deliver card.
  const onProveDone = useCallback(
    (raw: unknown) => {
      const result = asVerifyResult(raw);
      if (!result) {
        patch({
          prove: {
            ...stateRef.current.prove!,
            status: "failed",
            done: true,
            error: "no verify result returned",
          },
          running: false,
        });
        return;
      }
      // attach real per-skill baseline/refined to skills[] state
      setState((s) => ({
        ...s,
        skills: applyVerifyScores(s.skills, result),
        prove: { ...s.prove!, status: "done", result, done: true, t: 5400 },
        running: false,
      }));
      if (pollRef.current) pollRef.current.stop();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      push({ kind: "deliver" });
    },
    [push, patch]
  );

  // ── deliver: download / copy ──────────────────────────────────────────────
  const loadSpec = useCallback(async (): Promise<string> => {
    const s = stateRef.current;
    if (s.specMd) return s.specMd;
    if (!s.teamId || !s.agentId) return "";
    const md = await fetchSpecMd(s.teamId, s.agentId);
    if (typeof md === "string") {
      patch({ specMd: md });
      return md;
    }
    return "";
  }, [patch]);

  const download = useCallback(async () => {
    const md = await loadSpec();
    if (!md) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    a.download = "agent-spec.md";
    a.click();
    URL.revokeObjectURL(a.href);
  }, [loadSpec]);

  // Drop-in agent bundle (the landing-page promise): zip of <role-slug>/SKILL.md
  // files, loadable into Claude/Codex or any Agent Skills harness. Admin-gated
  // + LLM-generated server-side (first call per team takes a while; cached after).
  const downloadBundle = useCallback(async () => {
    if (!state.teamId || state.bundleBusy) return;
    patch({ bundleBusy: true });
    const r = await skillBundlesZip(state.teamId, state.adminToken);
    if (isApiError(r)) {
      patch({
        bundleBusy: false,
        tokenRejected: r.status === 401 ? true : state.tokenRejected,
      });
      return;
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(r);
    a.download = "team247-agent-skills.zip";
    a.click();
    URL.revokeObjectURL(a.href);
    patch({ bundleBusy: false });
  }, [state.teamId, state.adminToken, state.bundleBusy, state.tokenRejected, patch]);

  const copy = useCallback(async () => {
    const md = await loadSpec();
    if (!md) return;
    try {
      await navigator.clipboard.writeText(md);
    } catch {
      /* clipboard may be unavailable; the spec text is still loaded for download */
    }
    patch({ copied: true });
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => patch({ copied: false }), 1600);
  }, [loadSpec, patch]);

  // "Copy as prompt" (Iteration 5 — one-click export): fetches the selected
  // agent's composed SKILL.md via the skill-bundles JSON format (the same
  // compose_bundle the zip/download path uses, just format:"json" instead of
  // "zip" — see api.ts's skillBundleMd), caches it in skillMdByAgent so a
  // repeat copy of the same agent is instant, then writes the preamble +
  // markdown to the clipboard. Mirrors `copy`'s busy/flip pattern above.
  const copyPrompt = useCallback(async () => {
    const s = stateRef.current;
    if (!s.teamId || !s.agentId || s.copyPromptBusy) return;
    const key = `${s.teamId}:${s.agentId}`;
    let md = s.skillMdByAgent[key];
    if (!md) {
      patch({ copyPromptBusy: true });
      const r = await skillBundleMd(s.teamId, s.adminToken);
      if (isApiError(r)) {
        patch({
          copyPromptBusy: false,
          tokenRejected: r.status === 401 ? true : stateRef.current.tokenRejected,
        });
        return;
      }
      const bundle = r.bundles.find((b) => b.agent_id === s.agentId) ?? r.bundles[0];
      md = bundle?.skill_md ?? "";
      if (md) {
        const cached = md;
        setState((st) => ({
          ...st,
          skillMdByAgent: { ...st.skillMdByAgent, [key]: cached },
        }));
      }
      patch({ copyPromptBusy: false });
    }
    if (!md) return;
    try {
      await navigator.clipboard.writeText(COPY_AS_PROMPT_PREAMBLE + md);
    } catch {
      /* clipboard may be unavailable; the spec text is still cached for retry */
    }
    patch({ copyPromptCopied: true });
    if (copyPromptTimerRef.current) clearTimeout(copyPromptTimerRef.current);
    copyPromptTimerRef.current = setTimeout(() => patch({ copyPromptCopied: false }), 2000);
  }, [patch]);

  const adjust = useCallback(() => {
    push({ kind: "loadout" });
  }, [push]);

  // ── beta feedback (Iteration 1 — user-value loop) ────────────────────────
  // Optimistic: the row swaps to "thanks" immediately, then the POST fires.
  // Failure is silent (best-effort telemetry — never blocks or errors the
  // delivered-agent flow the user actually came for).
  const sendFeedback = useCallback((teamId: string, verdict: "up" | "down") => {
    setState((s) => ({
      ...s,
      feedbackByTeam: {
        ...s.feedbackByTeam,
        [teamId]: { ...s.feedbackByTeam[teamId], verdict, dismissed: false },
      },
    }));
    void apiSubmitFeedback(teamId, verdict, undefined, stateRef.current.adminToken);
  }, []);

  // Draft-only: updates the comment text as the user types, without POSTing
  // (submitFeedbackComment below sends it once they hit submit/Enter).
  const setFeedbackComment = useCallback((teamId: string, comment: string) => {
    setState((s) => ({
      ...s,
      feedbackByTeam: {
        ...s.feedbackByTeam,
        [teamId]: { ...s.feedbackByTeam[teamId], comment },
      },
    }));
  }, []);

  // Sends the current draft comment (same upserted row as the thumb click —
  // the server updates by (token_hash, team_id), never a second row).
  const submitFeedbackComment = useCallback((teamId: string) => {
    const existing = stateRef.current.feedbackByTeam[teamId];
    const comment = (existing?.comment ?? "").trim();
    if (!existing?.verdict || !comment) return;
    setState((s) => ({
      ...s,
      feedbackByTeam: {
        ...s.feedbackByTeam,
        [teamId]: { ...s.feedbackByTeam[teamId], comment, commentSubmitted: true },
      },
    }));
    void apiSubmitFeedback(teamId, existing.verdict, comment, stateRef.current.adminToken);
  }, []);

  const dismissFeedback = useCallback((teamId: string) => {
    setState((s) => ({
      ...s,
      feedbackByTeam: {
        ...s.feedbackByTeam,
        [teamId]: { ...s.feedbackByTeam[teamId], dismissed: true },
      },
    }));
  }, []);

  // ── try-your-agent chat (Iteration 2 — user-value loop) ─────────────────
  // One thread per (teamId, agentId). Appends the user's turn immediately
  // (optimistic), sends only the last 20 messages (server also caps at 20 —
  // this keeps the request small as a thread grows), then appends the
  // assistant's reply on success. A 429 (daily allowance) or any other
  // failure surfaces via chatByAgent[key].error and clears `busy` without
  // losing the user's turn already in the thread, so retry-by-resend works.
  const CHAT_HISTORY_LIMIT = 20;

  const sendAgentChat = useCallback((teamId: string, agentId: string, text: string) => {
    const clean = text.trim();
    if (!clean) return;
    const key = `${teamId}:${agentId}`;
    const existing = stateRef.current.chatByAgent[key];
    if (existing?.busy) return;

    const history = [...(existing?.messages ?? []), { role: "user" as const, content: clean }];
    setState((s) => ({
      ...s,
      chatByAgent: {
        ...s.chatByAgent,
        [key]: { messages: history, busy: true, error: undefined },
      },
    }));

    void (async () => {
      const payload = history.slice(-CHAT_HISTORY_LIMIT);
      const r = await agentChat(teamId, agentId, payload, stateRef.current.adminToken);
      if (isApiError(r)) {
        const msg =
          r.status === 429
            ? "Daily chat allowance reached — try again tomorrow"
            : "Could not reach your agent — try again";
        setState((s) => ({
          ...s,
          chatByAgent: {
            ...s.chatByAgent,
            [key]: { ...s.chatByAgent[key], busy: false, error: msg },
          },
        }));
        return;
      }
      setState((s) => {
        const cur = s.chatByAgent[key];
        const next = [
          ...(cur?.messages ?? history),
          { role: "assistant" as const, content: r.reply },
        ];
        return {
          ...s,
          chatByAgent: {
            ...s.chatByAgent,
            [key]: { messages: next, busy: false, error: undefined },
          },
        };
      });
    })();
  }, []);

  // ── real-inputs intake (Iteration 3 — user-value loop) ──────────────────
  // Full-replace PUT of the team's saved real inputs — the TeamCard builds
  // `items` from whichever "paste it now" boxes have content. Merged
  // client-side (by name) with whatever's already saved for this team
  // (including any ops-question answers from the Iteration 2 card below) so
  // this save never clobbers the other write path — PUT /inputs REPLACES,
  // it doesn't merge server-side. Re-generating (chat / skill-bundle export)
  // after this picks the inputs up automatically: both call compose_bundle
  // fresh, and skill_bundle_service's overlay cache key hashes
  // teams.user_inputs, so a save always busts the cache.
  const saveUserInputs = useCallback((teamId: string, items: UserInputItem[]) => {
    if (!teamId || items.length === 0) return;
    setState((s) => ({
      ...s,
      userInputsByTeam: { ...s.userInputsByTeam, [teamId]: { saving: true } },
    }));
    void (async () => {
      const existing = stateRef.current.userInputsByTeam[teamId]?.items ?? [];
      const merged = mergeUserInputsByName(existing, items);
      const r = await putTeamInputs(teamId, merged, stateRef.current.adminToken);
      if (isApiError(r)) {
        const raw = typeof r.detail === "string" ? r.detail : "";
        const msg =
          r.status === 422 && raw.includes("at most")
            ? "You've reached the limit of saved details for this team — your agents already have plenty to work with."
            : r.status === 422 && raw
              ? raw
              : "Could not save — try again";
        setState((s) => ({
          ...s,
          userInputsByTeam: { ...s.userInputsByTeam, [teamId]: { saving: false, error: msg } },
        }));
        return;
      }
      setState((s) => ({
        ...s,
        userInputsByTeam: {
          ...s.userInputsByTeam,
          [teamId]: { saving: false, saved: { count: r.count, bytes: r.bytes }, items: merged },
        },
      }));
    })();
  }, []);

  // ── ops-question interview: answer / save / dismiss (Iteration 2 — "make
  // it yours" UI) ───────────────────────────────────────────────────────────
  // answerOpsQuestion: local-only draft edit (mirrors TeamCard's `drafts`
  // convention) — clears a stale `saved` flag so editing after a save shows
  // the Save button live again instead of a stale ✓.
  const answerOpsQuestion = useCallback((teamId: string, idx: number, text: string) => {
    setState((s) => {
      const cur = s.opsQuestionsByTeam[teamId];
      if (!cur) return s;
      return {
        ...s,
        opsQuestionsByTeam: {
          ...s.opsQuestionsByTeam,
          [teamId]: { ...cur, answers: { ...cur.answers, [idx]: text }, saved: false },
        },
      };
    });
  }, []);

  // saveOpsAnswers: builds user_inputs entries from the non-empty answers,
  // merges them client-side (by name) into whatever's already saved for the
  // team (the "paste it now" panel's items, if any), and PUTs the full
  // merged list — PUT /inputs REPLACES, so a naive send-only-the-answers
  // call would silently drop any real inputs already saved via TeamCard.
  //
  // Iteration 4 (convergence + polish): once the PUT lands, re-fetch
  // /ops-questions — same convergence check confirmTranscriptItems already
  // does below — so the card reflects what the server now sees as still
  // missing, rather than sitting on the stale pre-save question list. The
  // fresh server set is authoritative; any prior question the server no
  // longer proposes AND that's still unanswered locally is carried forward
  // too (covers extraction-derived open_questions merged in client-side by
  // confirmTranscriptItems, which the server has no way to know about) — an
  // already-answered entry never lingers just because the server hasn't
  // caught up. Answer drafts are matched forward by question name so a
  // still-open question's typed-but-unsaved text isn't lost across the
  // re-fetch. If the fresh set comes back empty, the card converges to the
  // "done" state (see OpsQuestionsState.done / OpsQuestionsCard).
  const saveOpsAnswers = useCallback((teamId: string) => {
    const ops = stateRef.current.opsQuestionsByTeam[teamId];
    if (!ops || ops.busy) return;
    const newItems: UserInputItem[] = ops.questions
      .map((q, i) => ({ kind: q.kind, name: q.name, content: (ops.answers[i] ?? "").trim() }))
      .filter((it) => it.content.length > 0);
    if (newItems.length === 0) return;
    setState((s) => ({
      ...s,
      opsQuestionsByTeam: {
        ...s.opsQuestionsByTeam,
        [teamId]: { ...s.opsQuestionsByTeam[teamId], busy: true, error: undefined },
      },
    }));
    void (async () => {
      const existing = stateRef.current.userInputsByTeam[teamId]?.items ?? [];
      const merged = mergeUserInputsByName(existing, newItems);
      const r = await putTeamInputs(teamId, merged, stateRef.current.adminToken);
      if (isApiError(r)) {
        const raw = typeof r.detail === "string" ? r.detail : "";
        const msg =
          r.status === 422 && raw.includes("at most")
            ? "You've reached the limit of saved details for this team — your agents already have plenty to work with."
            : r.status === 422 && raw
              ? raw
              : "Could not save — try again";
        setState((s) => ({
          ...s,
          opsQuestionsByTeam: {
            ...s.opsQuestionsByTeam,
            [teamId]: { ...s.opsQuestionsByTeam[teamId], busy: false, error: msg },
          },
        }));
        return;
      }
      setState((s) => ({
        ...s,
        userInputsByTeam: {
          ...s.userInputsByTeam,
          [teamId]: { saving: false, saved: { count: r.count, bytes: r.bytes }, items: merged },
        },
      }));

      // Soft-stop: with this many saved details, the agents have plenty —
      // declare the interview complete rather than asking forever (the
      // generator rarely returns zero organically) or running users into
      // the 10-item cap.
      if (merged.length >= 8) {
        setState((s) => ({
          ...s,
          opsQuestionsByTeam: {
            ...s.opsQuestionsByTeam,
            [teamId]: {
              ...s.opsQuestionsByTeam[teamId],
              busy: false, saved: true, questions: [], done: true,
            },
          },
        }));
        return;
      }

      // Best-effort re-check: a failed re-fetch just leaves the question
      // list as it was pre-save (still accurate, just not re-verified)
      // rather than blocking or erroring the save that already succeeded.
      const refreshed = await opsQuestions(teamId);
      if (isApiError(refreshed)) {
        setState((s) => ({
          ...s,
          opsQuestionsByTeam: {
            ...s.opsQuestionsByTeam,
            [teamId]: {
              ...s.opsQuestionsByTeam[teamId],
              busy: false,
              saved: true,
              savedCount: newItems.length,
              error: undefined,
            },
          },
        }));
        return;
      }
      const serverQs: OpsQuestion[] = refreshed.questions ?? [];

      setState((s) => {
        const cur = s.opsQuestionsByTeam[teamId];
        const priorQuestions = cur?.questions ?? [];
        const priorAnswers = cur?.answers ?? {};
        const seen = new Set(serverQs.map((q) => q.name.trim().toLowerCase()));
        const leftover = priorQuestions.filter((q, i) => {
          const key = q.name.trim().toLowerCase();
          if (seen.has(key)) return false;
          seen.add(key);
          return !(priorAnswers[i] ?? "").trim();
        });
        const questions = [...serverQs, ...leftover];
        const answers: Record<number, string> = {};
        questions.forEach((q, i) => {
          const key = q.name.trim().toLowerCase();
          const oldIdx = priorQuestions.findIndex((pq) => pq.name.trim().toLowerCase() === key);
          if (oldIdx >= 0 && priorAnswers[oldIdx]) answers[i] = priorAnswers[oldIdx];
        });
        return {
          ...s,
          opsQuestionsByTeam: {
            ...s.opsQuestionsByTeam,
            [teamId]: {
              questions,
              answers,
              saved: true,
              savedCount: newItems.length,
              dismissed: cur?.dismissed ?? false,
              busy: false,
              error: undefined,
              done: questions.length === 0 && !!s.userInputsByTeam[teamId]?.saved,
            },
          },
        };
      });
    })();
  }, []);

  const dismissOpsCard = useCallback((teamId: string) => {
    setState((s) => ({
      ...s,
      opsQuestionsByTeam: {
        ...s.opsQuestionsByTeam,
        [teamId]: { ...s.opsQuestionsByTeam[teamId], dismissed: true },
      },
    }));
  }, []);

  // ── transcript extraction (Iteration 3 — OPERATIONS-INTAKE loop) ────────
  // "Or paste a meeting transcript instead": an alternative to answering the
  // ops-questions one at a time. POSTs the pasted text to
  // POST /api/team/{tid}/transcript, which returns a PROPOSAL only — nothing
  // is saved server-side by that call. The raw transcript text lives only in
  // this local `text` field (never sent anywhere except that one POST body)
  // until the panel is closed/confirmed/cancelled, at which point it's
  // discarded from state entirely — this hook never persists it.
  const emptyTranscriptState = (open: boolean): TranscriptState => ({
    open,
    text: "",
    busy: false,
    itemDrafts: {},
    removedItems: {},
  });

  const toggleTranscriptPanel = useCallback((teamId: string) => {
    setState((s) => {
      const cur = s.transcriptByTeam[teamId];
      return {
        ...s,
        transcriptByTeam: {
          ...s.transcriptByTeam,
          [teamId]: cur ? { ...cur, open: !cur.open } : emptyTranscriptState(true),
        },
      };
    });
  }, []);

  const setTranscriptText = useCallback((teamId: string, text: string) => {
    setState((s) => {
      const cur = s.transcriptByTeam[teamId] ?? emptyTranscriptState(true);
      return {
        ...s,
        transcriptByTeam: { ...s.transcriptByTeam, [teamId]: { ...cur, text, error: undefined } },
      };
    });
  }, []);

  const runTranscriptExtract = useCallback((teamId: string) => {
    const t = stateRef.current.transcriptByTeam[teamId];
    if (!t || t.busy) return;
    const text = t.text.trim();
    if (!text) return;
    setState((s) => ({
      ...s,
      transcriptByTeam: {
        ...s.transcriptByTeam,
        [teamId]: { ...t, busy: true, error: undefined, proposal: undefined },
      },
    }));
    void (async () => {
      const r = await extractTranscript(teamId, text, stateRef.current.adminToken);
      if (isApiError(r)) {
        const msg =
          r.status === 413
            ? "Transcript is too long — trim it and try again"
            : r.status === 422
              ? "Transcript text is required"
              : r.status === 401
                ? "beta token required"
                : "Could not extract from this transcript — try again";
        setState((s) => ({
          ...s,
          transcriptByTeam: {
            ...s.transcriptByTeam,
            [teamId]: { ...s.transcriptByTeam[teamId], busy: false, error: msg },
          },
        }));
        return;
      }
      const itemDrafts: Record<number, string> = {};
      r.items.forEach((it, i) => {
        itemDrafts[i] = it.content;
      });
      setState((s) => ({
        ...s,
        transcriptByTeam: {
          ...s.transcriptByTeam,
          [teamId]: {
            ...s.transcriptByTeam[teamId],
            busy: false,
            proposal: r,
            itemDrafts,
            removedItems: {},
          },
        },
      }));
    })();
  }, []);

  const editTranscriptItem = useCallback((teamId: string, idx: number, text: string) => {
    setState((s) => {
      const cur = s.transcriptByTeam[teamId];
      if (!cur) return s;
      return {
        ...s,
        transcriptByTeam: {
          ...s.transcriptByTeam,
          [teamId]: { ...cur, itemDrafts: { ...cur.itemDrafts, [idx]: text } },
        },
      };
    });
  }, []);

  const removeTranscriptItem = useCallback((teamId: string, idx: number) => {
    setState((s) => {
      const cur = s.transcriptByTeam[teamId];
      if (!cur) return s;
      return {
        ...s,
        transcriptByTeam: {
          ...s.transcriptByTeam,
          [teamId]: {
            ...cur,
            removedItems: { ...cur.removedItems, [idx]: !cur.removedItems[idx] },
          },
        },
      };
    });
  }, []);

  const cancelTranscript = useCallback((teamId: string) => {
    setState((s) => ({
      ...s,
      transcriptByTeam: { ...s.transcriptByTeam, [teamId]: emptyTranscriptState(false) },
    }));
  }, []);

  // confirmTranscriptItems: the non-removed proposal items (with any inline
  // edits applied) save via the SAME merge path as saveOpsAnswers/
  // saveUserInputs — PUT /inputs REPLACES, so this merges client-side with
  // whatever's already saved. After saving, it re-fetches ops-questions
  // (convergence — the server may now see fewer/zero gaps) AND merges the
  // extraction's own open_questions into the ops-questions card's question
  // list by name, so they become answerable through the exact same
  // answer/save flow as any other ops question — never dropping a question
  // the user may have already answered.
  const confirmTranscriptItems = useCallback((teamId: string) => {
    const t = stateRef.current.transcriptByTeam[teamId];
    if (!t || !t.proposal || t.busy) return;

    const items: UserInputItem[] = t.proposal.items
      .map((it, i) => ({
        kind: it.kind,
        name: it.name,
        content: (t.itemDrafts[i] ?? it.content).trim(),
      }))
      .filter((_, i) => !t.removedItems[i])
      .filter((it) => it.content.length > 0);
    const extractedOpenQs: OpsQuestion[] = t.proposal.open_questions.map((q) => ({
      kind: q.kind,
      name: q.name,
      question: q.question,
    }));

    setState((s) => ({
      ...s,
      transcriptByTeam: {
        ...s.transcriptByTeam,
        [teamId]: { ...t, busy: true, error: undefined },
      },
    }));

    void (async () => {
      if (items.length > 0) {
        const existing = stateRef.current.userInputsByTeam[teamId]?.items ?? [];
        const merged = mergeUserInputsByName(existing, items);
        const r = await putTeamInputs(teamId, merged, stateRef.current.adminToken);
        if (isApiError(r)) {
          const msg =
            typeof r.detail === "string" && r.status === 422
              ? r.detail
              : "Could not save — try again";
          setState((s) => ({
            ...s,
            transcriptByTeam: {
              ...s.transcriptByTeam,
              [teamId]: { ...s.transcriptByTeam[teamId], busy: false, error: msg },
            },
          }));
          return;
        }
        setState((s) => ({
          ...s,
          userInputsByTeam: {
            ...s.userInputsByTeam,
            [teamId]: { saving: false, saved: { count: r.count, bytes: r.bytes }, items: merged },
          },
        }));
      }

      // Convergence check: ask the server again now that the confirmed items
      // are saved — best-effort, a failed re-check just means we fall back
      // to the transcript's own open_questions below instead of blocking.
      const refreshed = await opsQuestions(teamId);
      const serverQs: OpsQuestion[] = !isApiError(refreshed) ? refreshed.questions ?? [] : [];

      setState((s) => {
        const cur = s.opsQuestionsByTeam[teamId];
        const base = cur?.questions ?? [];
        const seen = new Set(base.map((q) => q.name.trim().toLowerCase()));
        const toAppend: OpsQuestion[] = [];
        for (const q of [...serverQs, ...extractedOpenQs]) {
          const key = q.name.trim().toLowerCase();
          if (seen.has(key)) continue;
          seen.add(key);
          toAppend.push(q);
        }
        const questions = [...base, ...toAppend];
        return {
          ...s,
          opsQuestionsByTeam: {
            ...s.opsQuestionsByTeam,
            [teamId]: {
              questions,
              answers: cur?.answers ?? {},
              saved: false,
              // un-dismiss so newly-merged questions are actually visible —
              // only when there's something new to show.
              dismissed: toAppend.length > 0 ? false : (cur?.dismissed ?? false),
              busy: false,
              // Iteration 4 (convergence + polish) — same done semantics as
              // saveOpsAnswers: converged only if the merged set is actually
              // empty AND something has ever been saved for this team (a
              // transcript with only unconfirmed open_questions and nothing
              // saved yet should never show "done").
              done: questions.length === 0 && !!s.userInputsByTeam[teamId]?.saved,
            },
          },
          transcriptByTeam: { ...s.transcriptByTeam, [teamId]: emptyTranscriptState(false) },
        };
      });
    })();
  }, []);

  // ── starter gallery (Iteration 4 — user-value loop) ──────────────────────
  // "Customize for my business": prefills the landing task input with a
  // gallery archetype's use_case. Landing.tsx focuses its input field itself
  // right after calling this (it owns the DOM ref) — this just updates the
  // shared `input` state that Landing/Composer already render.
  const customizeFromGallery = useCallback(
    (useCase: string) => {
      patch({ input: useCase });
    },
    [patch]
  );

  // ── input / keyboard ──────────────────────────────────────────────────────
  const onInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setState((s) => ({ ...s, input: e.target.value }));
  }, []);
  const onKey = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") send();
    },
    [send]
  );

  // ── admin token setter ────────────────────────────────────────────────────
  const setAdminTokenState = useCallback((t: string) => {
    persistToken(t);
    setState((s) => ({ ...s, adminToken: t, tokenRejected: false }));
  }, []);

  // ── beta-access token gate: submit from the Landing gate form ────────────
  // Validates against /api/auth/status BEFORE persisting — an invalid/revoked
  // token is never written to localStorage, so the gate re-shows an inline
  // error instead of silently storing a token that will just 401 later.
  const submitBetaToken = useCallback(async (token: string) => {
    const clean = token.trim();
    if (!clean) return;
    const r = await authStatus(clean);
    if (!isApiError(r) && r.authenticated) {
      persistBetaToken(clean);
      setState((s) => ({ ...s, betaAuthenticated: true, betaTokenError: undefined }));
    } else {
      setState((s) => ({ ...s, betaTokenError: "invalid or revoked token" }));
    }
  }, []);

  return {
    state,
    send,
    pickRole,
    confirmTeam,
    toggleRole,
    setTargets,
    dec,
    inc,
    onBaseline,
    onSpecialize,
    onMax,
    generate,
    download,
    downloadBundle,
    copy,
    copyPrompt,
    adjust,
    sendFeedback,
    setFeedbackComment,
    submitFeedbackComment,
    dismissFeedback,
    sendAgentChat,
    saveUserInputs,
    answerOpsQuestion,
    saveOpsAnswers,
    dismissOpsCard,
    toggleTranscriptPanel,
    setTranscriptText,
    runTranscriptExtract,
    editTranscriptItem,
    removeTranscriptItem,
    cancelTranscript,
    confirmTranscriptItems,
    customizeFromGallery,
    onInput,
    onKey,
    setAdminTokenState,
    submitBetaToken,
  };
}

// PUT /api/team/{tid}/inputs REPLACES the team's whole saved set, so any
// caller that only has a partial list (the "paste it now" panel's artifact
// items, or the ops-questions card's answers) must merge client-side before
// sending — otherwise the second save silently wipes out the first. Union by
// `name` (case-insensitive, trimmed): an incoming item replaces an existing
// one with the same name in place, everything else is appended in order.
function mergeUserInputsByName(
  existing: UserInputItem[],
  incoming: UserInputItem[]
): UserInputItem[] {
  const merged = [...existing];
  for (const item of incoming) {
    const key = item.name.trim().toLowerCase();
    const idx = merged.findIndex((m) => m.name.trim().toLowerCase() === key);
    if (idx >= 0) merged[idx] = item;
    else merged.push(item);
  }
  return merged;
}

// Map a two-track VerifyResult onto the loadout skills[]: per-skill baseline +
// refined. Exec track → score (0..1)*100; rubric track → pct (single value,
// baseline = refined — honest, never blended with exec).
export function applyVerifyScores(
  skills: SkillState[],
  result: VerifyResult
): SkillState[] {
  const execByCode = new Map<string, (typeof result.exec_results)[number]>();
  for (const e of result.exec_results ?? []) {
    const key = e.code ?? e.skill;
    if (key) execByCode.set(key, e);
  }
  const rubBySkill = new Map<string, (typeof result.rubric_results)[number]>();
  for (const r of result.rubric_results ?? []) {
    rubBySkill.set(r.skill, r);
  }
  return skills.map((k) => {
    if (k.track === "exec") {
      const e = execByCode.get(k.code) ?? execByCode.get(k.name);
      if (!e) return k;
      const base = e.required_level > 0
        ? Math.round((Math.min(e.held_level, e.required_level) / e.required_level) * 100)
        : 0;
      const fin = Math.round(e.score * 100);
      return { ...k, base, fin };
    }
    // rubric
    const r = rubBySkill.get(k.name);
    if (!r) return k;
    const pct = Math.round(r.pct);
    return { ...k, base: pct, fin: pct };
  });
}

// Prove-card readiness headline = exec-track refined average (matches backend
// coverage_pct semantics — exec-only, never blended with rubric). Returns
// {base, fin} percent numbers for the deliver headline.
export function readiness(skills: SkillState[]): { base: number | null; fin: number | null } {
  const exec = skills.filter((k) => k.track === "exec" && k.fin != null);
  if (exec.length === 0) return { base: null, fin: null };
  const avg = (arr: number[]) => Math.round(arr.reduce((a, b) => a + b, 0) / arr.length);
  return {
    base: avg(exec.map((k) => k.base ?? 0)),
    fin: avg(exec.map((k) => k.fin ?? 0)),
  };
}
