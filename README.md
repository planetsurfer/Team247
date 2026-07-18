# Daytona — AgentProof

**AgentProof** — a verifiable proving ground for role-specialized agents, built for the **Daytona HackSprint** (one-day AI hackathon, NUS Singapore; sponsors Daytona + Kimi + Nosana + Oxylabs).

Point it at a role → it generates that role's skill battery → benchmarks a candidate agent by **executing** its work in isolated **Daytona** sandboxes (parallel fan-out) → produces a skills-gap report → **refines** the agent to close each gap → re-runs the benchmark and **shows the lift**. The demo money-shot is a before/after coverage bar per skill, where every green bar is code that provably ran in a Daytona sandbox.

**Status:** plan v3 (2026-07-15), ready for the day — the design was built to production between
Jul 13–15 as **MakeMyTeam** (separate repo, non-hackathon infra) and is fully de-risked; BUILD_GUIDE
§0.5 carries the validated decisions, the real bugs to avoid, and known-good constants. Day-of code
is built fresh on the hackathon stack (Daytona + Alibaba ModelStudio + Oxylabs).

- 📄 **[BUILD_GUIDE.md](./BUILD_GUIDE.md)** — the complete day-of guide: every file, prompt, SDK reference, run order, fallbacks, and demo script. Runs from a laptop only.
- Stack: Python · `daytona` SDK · Alibaba ModelStudio (OpenAI-compatible `compatible-mode` endpoint; per-function model routing via `LLM_*` env vars in `.env`).
- Working copy also in `~/Projects/AgentProof/`.
