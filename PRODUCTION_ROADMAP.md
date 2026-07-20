# Team247 — Production Roadmap

_From the production-readiness review, 2026-07-20. Plan: closed beta (issued tokens) → public (1 free generation, Google login) → per-generation billing. Target: AWS. Domains owned: team247.io / team247.app / team247.dev (registered via Route 53, 2026-07-20)._

## Where we are

The product core is beta-worthy: grounded catalog, guardrailed recommendation, verified skills, drop-in agent bundles, structured JSON logging, quality eval harnesses. The **platform is single-user localhost** by design (`jobs.py`: "fine for the v1 single-user localhost deployment"): no user model, one shared admin token, SQLite, in-process rate limits and jobs, LLM-generated code executed un-sandboxed in the app container.

## The five blockers

1. **No auth/user model** — `/api/team/recommend` (the most expensive endpoint, ~10–20 LLM calls) is completely open; only verify/skill-bundles are gated, by a single shared token.
2. **Verify executes LLM-generated code as the app user** (`config.py` LocalRunner: "NOT a true sandbox") — container has the LLM key in env; Dockerfile runs as root.
3. **Wallet-draining exposure** — every unauthenticated generation costs real LLM money; the only guard is a spoofable in-memory per-IP limit that breaks behind a load balancer.
4. **Single-process state** — SQLite + in-memory rate limits + in-process job threads: single container only; jobs lost on restart.
5. **No metering/billing substrate** — "charge per generation" needs a generations ledger (user, tokens, cost) that doesn't exist.

## Key decisions (made / recommended)

- **Login-first free tier** (recommended over anonymous-free-generation — the latter is trivially gamed via incognito).
- **Prepaid credit packs** over metered invoicing (simpler support, cash-positive).
- **Beta architecture: deliberately boring** — one EC2 + existing docker-compose + SQLite-on-EBS; keeps all single-process assumptions valid. Fargate/App Runner deferred (SQLite needs a local persistent disk).
- **Verify stays admin/beta-only** until it runs in real isolation; public launch does not need it (bundles don't depend on it).
- ⚠ **Confirm SkillsFuture dataset licensing permits commercial use before charging.** Business risk, settle early.

## P0 — before any external user (closed beta)

1. **Beta-token auth on every LLM endpoint** (recommend, intake/*, wire, render, distill, verify, skill-bundles): `beta_tokens` table (hashed token, label, active, quota), minting tool, auth dependency; per-**token** rate limiting (replaces per-IP; fixes the LB-spoofing problem).
2. **Secrets**: rotate `LLM_API_KEY` (was exposed in terminals during dev); strong admin token; secrets from SSM Parameter Store, never in the repo or image.
3. **Container hardening**: non-root `USER`, read-only FS where possible, resource limits, egress restricted to the LLM API.
4. **Deploy**: EC2 (t4g.small) + docker-compose; TLS on **team247.io** (Caddy/Let's Encrypt or CloudFront+ACM); Route 53 A record; security group 443 only, SSH via SSM Session Manager; SQLite on EBS with WAL + nightly snapshots; JSON logs → CloudWatch.
5. **Async recommend** (reuse the existing job pattern) so long generations survive proxy timeouts; UI progress.
6. Watch registrant verification email for the new domains (registry suspends unverified .io/.app/.dev).

## P1 — public free tier

7. Google login (Cognito federation) + `users` table; free-generation policy per decision above.
8. **Generations ledger** with real token counts/costs (extend `llm_call` logging) — price from data.
9. Hide admin/verify UI from non-admins; data retention job + privacy policy; CloudWatch alarms on `llm_call` error rate.

## P2 — charging & scale

10. Stripe prepaid credits; paywall gate at the service layer.
11. Postgres (RDS) migration via alembic + Redis (rate limits/sessions) + SQS worker for generations + multi-task ECS — only when load demands.
12. Wire the existing eval harnesses (persona battery, agent drop-in eval) into CI as a pre-deploy quality gate.

## AWS reference architecture

**Beta:** Route 53 (team247.io) → TLS edge (Caddy/LE on-box, or CloudFront+ACM) → EC2 t4g.small running docker-compose → SQLite on EBS (WAL, snapshots) · SSM for secrets + Session Manager · CloudWatch logs.

**Public:** CloudFront → ALB → ECS Fargate (2+ tasks) → RDS Postgres + ElastiCache Redis + SQS worker · Cognito (Google) · Stripe · verify isolated in its own task.

## Cost note

A full generation ≈ 10–20 kimi calls; skill bundles ≈ 2 calls/agent (heavily cached); verify is the expensive outlier (another reason to keep it beta-only). Meter real token usage from day one.
