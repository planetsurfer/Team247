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
  getAdminToken,
  isApiError,
  pollJob,
  putAgent,
  putTeamInputs,
  recommendAsync,
  renderAsync,
  seedTokenFromUrl,
  setAdminToken as persistToken,
  setBetaToken as persistBetaToken,
  skillBundlesZip,
  specMd as fetchSpecMd,
  submitFeedback as apiSubmitFeedback,
  teamSkills,
  verifyAsync,
} from "./api";
import type {
  Artifact,
  ChatThreadState,
  FeedbackState,
  Message,
  ProveState,
  RoleRow,
  SkillState,
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
  // beta feedback (Iteration 1 — user-value loop), keyed by teamId so the
  // DeliverCard's ask-row fires once per team even across re-renders.
  feedbackByTeam: Record<string, FeedbackState>;
  // try-your-agent chat (Iteration 2 — user-value loop), keyed by
  // `${teamId}:${agentId}` so history is independent per delivered agent.
  chatByAgent: Record<string, ChatThreadState>;
  // real-inputs intake (Iteration 3 — user-value loop) — TeamCard's "paste it
  // now" panel save flow, keyed by teamId.
  userInputsByTeam: Record<string, UserInputsSaveState>;
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
  feedbackByTeam: {},
  chatByAgent: {},
  userInputsByTeam: {},
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
    [push, patch]
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
  // `items` from whichever "paste it now" boxes have content. Re-generating
  // (chat / skill-bundle export) after this picks the inputs up automatically:
  // both call compose_bundle fresh, and skill_bundle_service's overlay cache
  // key hashes teams.user_inputs, so a save always busts the cache.
  const saveUserInputs = useCallback((teamId: string, items: UserInputItem[]) => {
    if (!teamId || items.length === 0) return;
    setState((s) => ({
      ...s,
      userInputsByTeam: { ...s.userInputsByTeam, [teamId]: { saving: true } },
    }));
    void (async () => {
      const r = await putTeamInputs(teamId, items, stateRef.current.adminToken);
      if (isApiError(r)) {
        const msg =
          typeof r.detail === "string" && r.status === 422
            ? r.detail
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
          [teamId]: { saving: false, saved: { count: r.count, bytes: r.bytes } },
        },
      }));
    })();
  }, []);

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
    adjust,
    sendFeedback,
    setFeedbackComment,
    submitFeedbackComment,
    dismissFeedback,
    sendAgentChat,
    saveUserInputs,
    onInput,
    onKey,
    setAdminTokenState,
    submitBetaToken,
  };
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
