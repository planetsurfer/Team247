// useChat — the single state machine for the Team247 chat flow.
//
// Ports the prototype's `Component` class (Team247 Chat.dc.html) — its `state`,
// `renderVals()` view-model, and `send/pickRole/confirmTeam/setTargets/generate/
// download/copy` handlers — to a React hook, replacing every mock with a live
// FastAPI call (see plan §useChat mapping table). The prove animation is driven
// from the real two-track verify job (polled + interpolated client-side).

import { useCallback, useEffect, useRef, useState } from "react";
import {
  asVerifyResult,
  catalogCard,
  catalogSearch,
  getAdminToken,
  isApiError,
  pollJob,
  putAgent,
  recommend,
  renderAsync,
  seedTokenFromUrl,
  setAdminToken as persistToken,
  skillBundlesZip,
  specMd as fetchSpecMd,
  teamSkills,
  verifyAsync,
} from "./api";
import type {
  Artifact,
  Message,
  ProveState,
  RoleRow,
  SkillState,
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
};

export function useChat() {
  const [state, setState] = useState<ChatState>(INITIAL);
  // mutable refs for the animation loop + poller + debounce so they survive
  // re-renders without being in deps.
  const rafRef = useRef<number | null>(null);
  const pollRef = useRef<ReturnType<typeof pollJob> | null>(null);
  const renderPollRef = useRef<ReturnType<typeof pollJob> | null>(null);
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
      if (putTimerRef.current) clearTimeout(putTimerRef.current);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
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

  const sendInternal = useCallback(
    async (text: string) => {
      push({ kind: "user", text }, { input: "", pending: true });
      const rec = await recommend(text);
      if (isApiError(rec)) {
        patch({ pending: false });
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
      });
      push({ kind: "team" });
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
    onInput,
    onKey,
    setAdminTokenState,
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
