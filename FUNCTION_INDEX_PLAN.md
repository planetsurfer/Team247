# Function-Indexed Retrieval (decompose-then-retrieve) — Implementation Plan

## Context / why

The v5 simulation run (`sim_results/postfix5`, 200 conversations, the full
six-version history in SESSION_LOG.md) measured the recommender at **2.87/5**
overall with `missing_key_role` on 142/200 and `generic_team` on 102/200.
Diagnosis: retrieval is IDF-weighted keyword overlap, which cannot bridge
(1) **vocabulary mismatch** ("chase up ppl who owe me" shares zero stems with
"Accounts Receivable Management"), (2) **one query for a multi-function task**
(the dominant function crowds out the others), (3) **no guarantee** that the
candidate slate contains a role for every function the composer is told to
staff. This plan replaces search-and-hope with: decompose the brief into
canonical functions → look up roles per function from a precomputed index →
slate covers every function by construction.

Targets (vs postfix5 baseline, same personas/seed): `missing_key_role` rate
<20% (from 71%), coverage ~4.0 (from 2.86), overall ≥3.5. Structural
invariants that must NOT weaken: 100%-team guarantee (≥1 real catalog role
with skills, always), roles only from the catalog, sector-UNION in
team_service (stated industry + inferred functional sectors — never one
overriding the other), no duplicate roles, 1–8 team size.

## Phase A — offline: taxonomy + role tagging

**A1. Function taxonomy** → `data/function_taxonomy.json` (committed,
human-editable): 150–250 canonical business functions, each
`{id: "accounts-receivable", name, definition, everyday_phrasings: [...]}`.
Bootstrap: sample skill titles + K&A items across all 39 sectors (from
`role_skills` + `ka_items`, joined on `code`) → one LLM pass drafts the
taxonomy → a second pass tags a ~100-role sample and adds any missing
functions. Kebab-case ids; ids are the stable contract.

**A2. Role tagging** → new SQLite table
`role_functions(role_id INTEGER, function_id TEXT, PRIMARY KEY(role_id, function_id))`
(+ index on function_id), created via idempotent `CREATE TABLE IF NOT EXISTS`
in the build script (matches existing inline-DDL style; alembic has no
versions to extend). New root script **`build_function_index.py`** (copy the
`distill_enrichment.py` pattern: one-time, idempotent, `--force`, resumable —
skip roles already tagged): for each of the 1910 roles, prompt = role name +
description + skill titles (+ up to ~10 K&A items) + the full taxonomy id
list → STRICT JSON `{"functions": ["id", ...]}` (3–8, validated ⊆ taxonomy).
Purpose `function_tag`, env `LLM_MODEL_FUNCTION_TAG` (cheap model fine —
closed-set classification). ~1910 calls, resumable across sessions.

**A3. Audit** (same script, `--audit`): report functions with 0 roles
(taxonomy gaps → merge/remove), roles with 0 functions (retry list), and a
histogram. Iterate A1/A2 until <5 orphan functions and 0 untagged roles.

## Phase B — query time: decompose → lookup → compose

**B1.** New contract `llm_contracts.identify_functions(brief_text, taxonomy)`
→ validated `{"functions": [ids]}`, 1–4 ids from the closed list, purpose
`identify_functions`, env `LLM_MODEL_IDENTIFY_FUNCTIONS`. Prompt must ask for
functions the work REQUIRES, including implied ones (an auditor visit implies
compliance-prep). Failure → `[]` and the pipeline falls back to current
retrieval (never break the 100% guarantee).

**B2.** `team_service.recommend`: after sector-union computation, call
identify_functions on the same query text. For each function, look up
`role_functions ⋈ roles` → candidate `(role, sector)` pairs, ranked:
in-preferred-sector first, then by the existing IDF text score. Take top ~3
per function as **seed candidates**.

**B3.** `classify.recommend_roles(task, k, preferred_sectors, must_include=None)`:
new optional param — a list of `(role, sector)` pairs merged into the slate
BEFORE the chooser LLM (dedup; slate cap grows to ~18; remaining slots filled
by current IDF+sector-reserve retrieval, which also covers taxonomy misses).
classify stays framework-only; the app-DB lookup lives in team_service.

**B4.** Composer alignment: `llm_contracts.team_recommend` gains the function
list — "FUNCTIONS THE TEAM MUST COVER: ...; staff one agent per function; if
no candidate covers a function, still deliver the best team and name the gap
in that agent's rationale." API response adds `functions_needed` and
`functions_uncovered` (roles exist for honesty/debug; SPA rendering out of
scope). Persist both in `recommendation_json`.

**B5.** Env rows in `.env` + `.env.example`: `LLM_MODEL_FUNCTION_TAG`,
`LLM_MODEL_IDENTIFY_FUNCTIONS`.

## Phase C — measurement (harness already exists)

**C1.** `tests/simulation/judge.py` structural check: from the recommend
response, `functions_needed` non-empty AND every needed function has ≥1
candidate in `recommendation_raw.candidates` tagged with it (deterministic
per-function recall — no judge involved). `report.py`: per-function recall
line + `functions_uncovered` counts.

**C2.** A/B protocol: same seed/personas as always. Smoke (2 personas) →
24-persona paired batch vs the same slice of `sim_results/postfix5` → full
100-persona both-track run. Paired deltas only (single-run swing is ±0.3).

## Verification ladder

1. Offline: taxonomy audit clean; stub `classify.llm_json` and assert
   must_include roles reach the slate; `identify_functions` validated on the
   known worst cases (invoice-chasing → accounts-receivable; landscape report
   → report-collation; "auditor coming" → compliance-prep, the implied case).
2. Live spot-checks (server restarted): agrifood-3, design-2, landscape-2,
   biopharmaceuticals-manufacturing-2, logistics-1 (trucking must stay 4+).
3. 24-persona paired batch → keep/revert decision per targets above.
4. Full 200-conversation run → final numbers vs postfix5 in SESSION_LOG.

## Key files

- NEW: `data/function_taxonomy.json`, `build_function_index.py`
- EDIT: `app/llm_contracts.py` (identify_functions + team_recommend prompt),
  `app/services/team_service.py` (B2 seed lookup + response fields),
  `classify.py` (must_include merge), `tests/simulation/judge.py`,
  `tests/simulation/report.py`, `.env`/`.env.example`
- BASELINE: commit the pending v5 union-sector fix in
  `app/services/team_service.py` BEFORE starting, so every loop iteration
  has a clean revert point.

## Cost / time

Taxonomy ~10 LLM calls; tagging ~1910 cheap calls (single-digit $, resumable);
per-recommend adds ONE LLM call (identify_functions). Full A/B run ≈ 2–3h
background as before.
