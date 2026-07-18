# AgentProof Implementation Plan (Daytona HackSprint — day-of build)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build AgentProof — generate a role-grounded agent spec from the SkillsFuture Skills Framework, prove it by executing its work per skill in Daytona sandboxes, refine it to close gaps, and show a two-track (executed / rubric) role-readiness lift on a dashboard.

**Architecture:** A flat Python CLI pipeline (`framework → battery → agent → candidate → assess → gap → refine → run → dashboard`), per BUILD_GUIDE.md §3. The generated agent markdown is used *as the candidate's system prompt* (the graded object IS the deliverable). Executable skills are sandbox-graded; everything else is LLM-judged against the framework's own K&A rubric and badged "not execution-verified" — two numbers, never blended.

**Tech Stack:** Python 3 · `daytona` SDK (sandboxes) · Alibaba ModelStudio via OpenAI-compatible client (`token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`; per-function model routing via `LLM_*` env vars) · `openpyxl` (reads the SkillsFuture xlsx directly) · `requests` (Oxylabs Realtime API) · stdlib HTML dashboard.

---

## Context

This is the day-of build plan for the **Daytona HackSprint** (one-day hackathon, NUS Singapore; sponsors Daytona + Kimi + Nosana + Oxylabs). The repo at `~/Daytona` contains **specs and seed assets only — zero code**. The design (BUILD_GUIDE.md v3) was already built to production Jul 13–15 as *MakeMyTeam* on different infra, so every architectural decision is de-risked; this plan transcribes that validated design into an executable, phased build on the hackathon stack, folding in the seven production bugs (§0.5) at the exact task where each would otherwise bite.

Per BUILD_GUIDE §0.5, day-of code is **built fresh** — check event rules before reusing verbatim source. This plan therefore contains complete code so the build is a fast, low-risk transcription on the day.

**Locked scope (§9):** ONE role (hardwired demo role), single agent, executable subset (~5 skills), Mode A entry, 1 refine round, two-track scorecard is CORE. Team/seam story is told with production receipts (§7.5), never rebuilt. Dashboard working by ~15:30; last hour is rehearsal.

## Ground truth (verified against the real xlsx, 2026-07-18)

- Sheets: `Job Role_Description`, `Job Role_CWF_KT`, `Job Role_TSC_CCS`, `TSC_CCS_Key`, `TSC_CCS_Key_Retired`, `TSC_CCS_K&A` — exactly as §5.
- Column orders (0-indexed, used verbatim in `framework.py`):
  - `Job Role_Description`: Sector, Track, Job Role, Job Role Description, Performance Expectation
  - `Job Role_CWF_KT`: Sector, Track, Job Role, Critical Work Function, Key Tasks
  - `Job Role_TSC_CCS`: Sector, Track, Job Role, TSC_CCS Title, TSC_CCS Type, Proficiency Level, TSC_CCS Code
  - `TSC_CCS_K&A`: Type, Code, Sector, Category, Title, Description, Proficiency Level, Proficiency Description, Knowledge / Ability Items, Knowledge / Ability Classification
  - `TSC_CCS_Key_Retired`: TSC Code, Sector, Category, Title, Description, Type, Retired Date
- Demo role resolves to exactly **14 skill rows**: `('Infocomm Technology', 'Data and Artificial Intelligence', 'Data Analyst / Associate Data Engineer')` — hardwire these exact strings.
- Proficiency levels are **strings**: `'1'`–`'6'` plus legacy `'Basic'/'Intermediate'/'Advanced'` → `normalize_level()` is mandatory.
- `battery_seed.json` (3 items: Data Engineering `ICT-DIT-2005-1.1`, Database Administration `ICT-OUS-2006-1.1`, Data Analytics `ICT-BIN-2104-1.1`) — all three codes ARE in the demo role's 14, so the seed ∩ role filter passes them.
- `agent_seed.md` ends with the `## Learned skill guidance` anchor + `_None yet._` — usable both as agent-gen fallback and as a deliberately-terse v0.
- `dashboard_mockup.html` is a finished two-track stage visual (executed ring `#pctBig` + rubric stat `#rubBig`, per-sandbox cards) with hardcoded demo data — keep as the polished backup visual; `dashboard.py` generates its own real-data page.

## File structure (project root: `~/Projects/AgentProof`)

```
~/Projects/AgentProof/
├── .env                  # DAYTONA_API_KEY, MOONSHOT_API_KEY, KIMI_MODEL, OXYLABS_USERNAME/PASSWORD
├── requirements.txt
├── config.py             # env, Kimi client + kimi_chat/kimi_json, make_daytona, constants
├── smoke_test.py         # FIRST: proves both APIs
├── framework.py          # xlsx data layer (resolve_role/get_skills/get_context/get_ka/…)
├── events.py             # ~12-line lifecycle emitter (jsonl + print)
├── candidate.py          # the identity swap: agent_spec AS system prompt
├── assess.py             # sandbox run + GRADE parse + level bands + lifecycle events
├── gap.py                # coverage math + terminal report
├── agent.py              # spec generation (the deliverable)
├── refine.py             # gap → instruction → patch under the anchor
├── run.py                # orchestrator; emits agent_final.md, output.json, events.jsonl
├── battery.py            # STRETCH-A: Kimi-generated grounded battery + rubric_score (CORE)
├── oxylabs_fetch.py      # STRETCH-B: postings digest (forced-visible), salary, career map
├── classify.py           # STRETCH-C: Mode B task → real roles
├── dashboard.py          # real-data HTML dashboard, auto-opens
├── test_local.py         # offline unit checks (grade parsing, bands, anchor safety)
└── data/
    ├── skillsfuture.xlsx     # copy of the 13MB dataset
    ├── battery_seed.json     # copy from ~/Daytona
    ├── agent_seed.md         # copy from ~/Daytona
    └── dashboard_mockup.html # copy from ~/Daytona (backup visual)
```

Production-verified constants (§0.5 — start here, don't rediscover): battery-gen temp **0.2** · judge temp **0.0** · `MAX_PARALLEL` **4** · sandbox timeout **20s** · **1** refine round · seed battery **3** items.

---

## Phase 0 — Tonight (pre-day prep; NO product code)

*Writing product code before the event may violate hackathon rules — this phase only stages environment and assets.*

### Task 1: Stage the environment

- [ ] **Step 1:** Create the project skeleton and copy assets:
```bash
mkdir -p ~/Projects/AgentProof/data
cd ~/Projects/AgentProof && git init
cp ~/Daytona/jobsandskills-skillsfuture-skills-framework-dataset.xlsx data/skillsfuture.xlsx
cp ~/Daytona/battery_seed.json ~/Daytona/agent_seed.md ~/Daytona/dashboard_mockup.html data/
printf 'openai\npython-dotenv\nrequests\nopenpyxl\ndaytona\n' > requirements.txt
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```
- [ ] **Step 2:** If `pip install daytona` fails: `pip install daytona-sdk` (§8 fallback — `config.make_daytona()` already handles both import names).
- [ ] **Step 3:** Create `.env` with placeholder keys (fill real ones at the workshop ~10:30):
```
DAYTONA_API_KEY=
MOONSHOT_API_KEY=
KIMI_MODEL=kimi-k2-0711-preview
OXYLABS_USERNAME=
OXYLABS_PASSWORD=
```
`KIMI_MODEL` **must be re-verified at the workshop** (guide §sources suggests something like `kimi-k2.7-code` may exist by event day).
- [ ] **Step 4:** `echo -e '.venv/\n.env\n__pycache__/' > .gitignore && git add -A && git commit -m "chore: scaffold, seeds, data"`
- [ ] **Step 5:** Pre-open the §7.5 receipts in browser tabs for the demo: `~/Daytona/seam_architecture.html`, `receipts.html`, and (from the production repo) `ProveTeam.tsx` / `test_seam_sandbox.py` if the external drive is mounted.
- [ ] **Step 6:** Decide the **Nosana line** now (§10): Option A (spoken roadmap — recommended) unless the booth template makes Option B trivial.

---

## Phase 1 — H0 (~10:30–11:00): config + smoke test

### Task 2: `config.py`

**Files:** Create: `config.py`

- [ ] **Step 1:** Write `config.py`:
```python
import os
from dotenv import load_dotenv
load_dotenv()

KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k2-0711-preview")   # VERIFY at workshop
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "4"))     # §0.5 verified; drop to 1-2 on quota errors
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "20"))
TARGET_COVERAGE = 90.0
MAX_ROUNDS = 2            # baseline + 1 refine round (production: 1 round gives visible lift)
DATA_XLSX = os.getenv("DATA_XLSX", "data/skillsfuture.xlsx")

from openai import OpenAI
_kimi = OpenAI(api_key=os.environ["MOONSHOT_API_KEY"], base_url=KIMI_BASE_URL)

def kimi_chat(messages, temperature=0.3, json_mode=False, max_tokens=4096):
    kw = dict(model=KIMI_MODEL, messages=messages,
              temperature=temperature, max_tokens=max_tokens)
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    return _kimi.chat.completions.create(**kw).choices[0].message.content

def kimi_json(messages, temperature=0.2, validate=None, retries=2, max_tokens=4096):
    """Strict JSON with client-side validate-and-retry (§0.5 bug 5)."""
    import json as _json
    last = None
    for _ in range(retries + 1):
        try:
            obj = _json.loads(kimi_chat(messages, temperature, json_mode=True,
                                        max_tokens=max_tokens))
            if validate is None or validate(obj):
                return obj
            last = "validation failed"
        except Exception as e:
            last = str(e)
    raise ValueError(f"kimi_json failed after retries: {last}")

def make_daytona():
    try:
        from daytona import Daytona, DaytonaConfig
    except ImportError:                                   # §8 fallback package name
        from daytona_sdk import Daytona, DaytonaConfig
    return Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))
```

### Task 3: `smoke_test.py` — RUN FIRST, before building anything else

**Files:** Create: `smoke_test.py`

- [ ] **Step 1:** Write `smoke_test.py`:
```python
from config import kimi_chat, make_daytona

print("Kimi:", kimi_chat([{"role": "user", "content": "Reply with exactly: KIMI OK"}],
                         temperature=0)[:40])
d = make_daytona()
sb = d.create()
try:
    r = sb.process.code_run("print(2+2)")
    print("Daytona:", (getattr(r, "result", "") or "").strip(), "| sandbox", sb.id)
finally:
    try:
        sb.delete()
    except Exception:
        d.delete(sb)                 # SDK versions differ on where delete lives
print("SMOKE OK")
```
- [ ] **Step 2:** Run: `python smoke_test.py` — Expected: `Kimi: KIMI OK`, `Daytona: 4 | sandbox <id>`, `SMOKE OK`. If Kimi 404s on the model id, fix `KIMI_MODEL` in `.env` (ask at the Kimi booth). If `code_run`'s signature differs (`timeout=` kw), note the correct form now — `assess.py` uses it.
- [ ] **Step 3:** `git add -A && git commit -m "feat: config + smoke test green"`

---

## Phase 2 — (~11:00–11:40): `framework.py` (the data layer)

### Task 4: `framework.py`

**Files:** Create: `framework.py`

- [ ] **Step 1:** Write `framework.py` (column indices match the verified sheet headers above):
```python
import openpyxl
from functools import lru_cache
from config import DATA_XLSX

DEMO_SECTOR = "Infocomm Technology"
DEMO_TRACK = "Data and Artificial Intelligence"
DEMO_ROLE = "Data Analyst / Associate Data Engineer"    # exact string, verified in xlsx

EXEC_KEYWORDS = ("data engineering", "database administration",
                 "data analytics", "data visualisation")

_LEGACY = {"basic": 2, "intermediate": 4, "advanced": 6}   # only affects rubric-only skills

def normalize_level(v):
    """Collapse dual scale ('1'-'6' strings + Basic/Intermediate/Advanced) to int. Never hardcode a max."""
    if v is None:
        return 0
    s = str(v).strip().lower()
    if s in _LEGACY:
        return _LEGACY[s]
    try:
        return int(float(s))
    except ValueError:
        return 0

@lru_cache(maxsize=None)
def _sheet(name):
    """One full read per sheet, cached. K&A sheet (~150K rows) takes ~15-30s on first touch."""
    wb = openpyxl.load_workbook(DATA_XLSX, read_only=True)
    ws = wb[name]
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True)
            if any(c is not None for c in r)]
    wb.close()
    return rows

def resolve_role(query):
    q = query.strip().lower()
    for sector, track, role, *_ in _sheet("Job Role_Description"):
        if role and q in role.lower():
            return sector, track, role
    print(f"WARN: role '{query}' not found; falling back to demo role")   # §8: never crash
    return DEMO_SECTOR, DEMO_TRACK, DEMO_ROLE

def retired_codes():
    return {r[0] for r in _sheet("TSC_CCS_Key_Retired") if r[0]}

def get_skills(role):
    dead = retired_codes()
    out = []
    for _sec, _trk, jr, title, typ, level, code in _sheet("Job Role_TSC_CCS"):
        if jr == role and code not in dead:
            out.append({"skill": title, "type": typ,
                        "required_level": normalize_level(level), "code": code})
    return out

def get_context(role):
    desc, perf = "", ""
    for _sec, _trk, jr, d, p in _sheet("Job Role_Description"):
        if jr == role:
            desc, perf = d or "", p or ""
            break
    cwfs = {}
    for _sec, _trk, jr, cwf, kt in _sheet("Job Role_CWF_KT"):
        if jr == role and cwf:
            cwfs.setdefault(cwf, []).append(kt or "")
    return {"description": desc, "performance_expectation": perf,
            "critical_work_functions": [{"cwf": k, "key_tasks": v} for k, v in cwfs.items()]}

def select_executable(skills, k=5):
    """Skills a coding grader can honestly test. Demo role yields 5: DE L2, DBA L2, DA L2, DA L3, DV L3."""
    return [s for s in skills
            if any(kw in s["skill"].lower() for kw in EXEC_KEYWORDS)][:k]

def get_ka(code, level):
    """Official per-level Knowledge & Ability checklist — the rubric that grounds every graded task."""
    items, prof = [], ""
    for _typ, c, _sec, _cat, _title, _desc, lvl, pdesc, item, kind in _sheet("TSC_CCS_K&A"):
        if c == code and normalize_level(lvl) == level and item:
            items.append({"item": str(item).strip(),
                          "kind": str(kind or "").strip().lower()})
            prof = pdesc or prof
    return {"proficiency_description": prof or "", "items": items}

def get_sector(role):
    for sector, track, jr, *_ in _sheet("Job Role_Description"):
        if jr == role:
            return {"sector": sector, "track": track}
    return {"sector": DEMO_SECTOR, "track": DEMO_TRACK}
```
- [ ] **Step 2:** Verify the data layer reads the real role (guide §6 step 3):
```bash
python -c "
import framework as f
s, t, r = f.resolve_role('data analyst / associate data engineer')
print(s, '|', t, '|', r)
sk = f.get_skills(r); print(len(sk), 'skills'); ex = f.select_executable(sk)
print('executable:', [(x['skill'], x['required_level'], x['code']) for x in ex])
ka = f.get_ka('ICT-DIT-2005-1.1', 2); print('K&A items:', len(ka['items']), '| e.g.', ka['items'][0]['item'][:60])
"
```
Expected: 14 skills; 5 executable (Data Engineering L2, Database Administration L2, Data Analytics L2, Data Analytics L3, Data Visualisation L3); K&A items > 0 with a real ability line (e.g. "Apply appropriate data collection tools and techniques…").
- [ ] **Step 3:** `git add -A && git commit -m "feat: framework data layer reads real role"`

---

## Phase 3 — (~11:40–12:40): the seed loop end-to-end for ONE skill

### Task 5: `events.py` (the ~12-line lifecycle emitter)

**Files:** Create: `events.py`

*Built now (not §6 step 8) because `assess.py` imports it and its print half doubles as run progress. The `demo_theatre` replay stays deferred to Phase 11.*

- [ ] **Step 1:** Write `events.py`:
```python
import json, time

PATH = "events.jsonl"

def emit(event, **kw):
    """Log a real lifecycle transition. Never raises, never blocks (BUILD_GUIDE §3)."""
    try:
        print(f"  [{event}] " + " ".join(f"{k}={v}" for k, v in kw.items()), flush=True)
        with open(PATH, "a") as f:
            f.write(json.dumps({"ts": round(time.time(), 3),
                                "event": event, **kw}) + "\n")
    except Exception:
        pass
```

### Task 6: `candidate.py` (the identity swap — the crux)

**Files:** Create: `candidate.py`

- [ ] **Step 1:** Write `candidate.py`:
```python
import re
from config import kimi_chat

_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n|```\s*$", re.MULTILINE)

def candidate_solve(task_prompt, agent_spec):
    """The generated markdown IS the system prompt — no hidden base prompt (BUILD_GUIDE §4)."""
    code = kimi_chat(
        [{"role": "system", "content": agent_spec},
         {"role": "user", "content": task_prompt +
          "\n\nReturn ONLY the Python code defining solve(...). "
          "No explanations, no markdown fences."}],
        temperature=0.2)
    return _FENCE.sub("", code).strip()
```

### Task 7: `assess.py`

**Files:** Create: `assess.py`

- [ ] **Step 1:** Write `assess.py`:
```python
import json, time
from config import SANDBOX_TIMEOUT
from candidate import candidate_solve
from events import emit

def parse_grade(stdout):
    """Bottom-up scan for the GRADE line; clamp; garbled/missing -> 0 + note (§0.5 bug 6)."""
    for line in reversed((stdout or "").strip().splitlines()):
        if line.startswith("GRADE:"):
            try:
                s = float(json.loads(line[len("GRADE:"):])["score"])
                return max(0.0, min(1.0, s)), None
            except Exception as e:
                return 0.0, f"garbled GRADE: {e}"
    return 0.0, "no GRADE line in output"

def held_level(score, required):
    """§0.5 bands: >=0.7 full level · >=0.4 level-1 · else level-2, floored at 0."""
    if score >= 0.7:
        return required
    if score >= 0.4:
        return max(required - 1, 0)
    return max(required - 2, 0)

def assess_skill(daytona, item, agent_spec):
    """Fresh sandbox -> candidate code + grader together -> parse GRADE -> teardown in finally."""
    skill, req = item["skill"], item["required_level"]
    sandbox, sid, t0, err = None, "?", time.time(), None
    score = 0.0
    try:
        sandbox = daytona.create()
        sid = sandbox.id
        emit("spawn", skill=skill, sandbox_id=sid)
        code = candidate_solve(item["task_prompt"], agent_spec)
        emit("assign", skill=skill, sandbox_id=sid)
        emit("execute", skill=skill, sandbox_id=sid)
        resp = sandbox.process.code_run(code + "\n\n" + item["grader_code"],
                                        timeout=SANDBOX_TIMEOUT)
        score, err = parse_grade(getattr(resp, "result", ""))
        emit("grade", skill=skill, sandbox_id=sid, detail=f"score={score}")
    except Exception as e:
        err = str(e)
    finally:
        if sandbox is not None:
            emit("teardown", skill=skill, sandbox_id=sid)
            try:
                sandbox.delete()
            except Exception:
                try:
                    daytona.delete(sandbox)
                except Exception:
                    pass
    held = held_level(score, req)
    emit("report", skill=skill, sandbox_id=sid,
         detail=f"held L{held} vs required L{req}")
    return {"skill": skill, "code": item.get("code"), "required_level": req,
            "score": score, "held_level": held, "gap": max(0, req - held),
            "error": err, "sandbox_id": sid,
            "seconds": round(time.time() - t0, 1),
            "task_prompt": item["task_prompt"]}
```
*(If smoke-test showed `code_run` takes no `timeout=` kwarg in this SDK version, drop the kwarg — the graders' internal try/excepts still bound behaviour.)*

### Task 8: offline unit checks

**Files:** Create: `test_local.py`

- [ ] **Step 1:** Write `test_local.py` (no network needed):
```python
from assess import parse_grade, held_level

assert parse_grade('noise\nGRADE:{"score": 0.75}')[0] == 0.75
assert parse_grade('GRADE:{"score": 2.0}')[0] == 1.0          # clamped
assert parse_grade("no grade here") == (0.0, "no GRADE line in output")
assert parse_grade("GRADE:{bad json}")[0] == 0.0
assert held_level(0.7, 3) == 3
assert held_level(0.4, 3) == 2
assert held_level(0.1, 3) == 1
assert held_level(0.1, 1) == 0                                 # floor at 0
print("LOCAL TESTS OK")
```
- [ ] **Step 2:** Run: `python test_local.py` — Expected: `LOCAL TESTS OK`.

### Task 9: prove ONE skill end-to-end (seed battery + seed spec)

- [ ] **Step 1:** Run one seed item through the whole chain:
```bash
python -c "
import json
from config import make_daytona
from assess import assess_skill
item = json.load(open('data/battery_seed.json'))[0]
spec = open('data/agent_seed.md').read()
r = assess_skill(make_daytona(), item, spec)
print({k: r[k] for k in ('skill','score','held_level','gap','error','seconds')})
"
```
Expected: lifecycle lines `[spawn] → [assign] → [execute] → [grade] → [teardown] → [report]` print, and a dict with a real `score` (any value — even 0.0 proves the chain; the seed tasks are deliberately tricky so a sub-1.0 baseline is *good* demo drama).
- [ ] **Step 2:** `git add -A && git commit -m "feat: one skill assessed e2e (spec-as-system-prompt)"`

---

## Phase 4 — (~12:40–13:20): `gap.py` + `agent.py` + `run.py` fan-out → baseline report

### Task 10: `gap.py`

**Files:** Create: `gap.py`

- [ ] **Step 1:** Write `gap.py`:
```python
def gap_report(results):
    """coverage = (1 - sum(gap)/sum(required)) * 100 — scale-agnostic (works on 1-6)."""
    total_req = sum(r["required_level"] for r in results) or 1
    total_gap = sum(r["gap"] for r in results)
    coverage = round((1 - total_gap / total_req) * 100, 1)
    gaps = sorted((r for r in results if r["gap"] > 0), key=lambda r: -r["gap"])
    return {"coverage_pct": coverage, "gaps": gaps, "results": results}

def print_report(report, label=""):
    print(f"\n=== {label}  role-readiness (executed): {report['coverage_pct']}% ===")
    for r in report["results"]:
        bar = "#" * int(r["score"] * 20)
        flag = "   <-- gap" if r["gap"] else ""
        note = f"  ({r['error']})" if r.get("error") else ""
        print(f"  {r['skill'][:28]:28} L{r['held_level']}/{r['required_level']}"
              f"  score {r['score']:.2f} |{bar:<20}|{flag}{note}")
```

### Task 11: `agent.py` (generates the deliverable)

**Files:** Create: `agent.py`

- [ ] **Step 1:** Write `agent.py`:
```python
from config import kimi_chat

ANCHOR = "## Learned skill guidance"

def generate_agent(role, skills, context, custom_instructions="", sector=""):
    skills_tbl = "\n".join(f"| {s['skill']} | {s['code']} | L{s['required_level']} |"
                           for s in skills)
    cwf = "\n".join(f"- {c['cwf']}: " + "; ".join(c["key_tasks"][:3])
                    for c in context["critical_work_functions"][:6])
    prompt = f"""Write an agent specification in markdown for an AI agent performing this role.

Role: {role} (source: SkillsFuture Skills Framework, sector: {sector})
Role description: {context['description']}

Official required skills — reproduce EXACTLY this table; never add, drop, or rename a skill:
| Skill | Code | Required level |
|---|---|---|
{skills_tbl}

Critical work functions (distill a working method from these):
{cwf}

Custom instructions from the requester (include verbatim in their own section):
{custom_instructions or '(none)'}

Structure — exactly these sections, in order:
# Agent Specification — {role}
## Role
## Official required skills
## Operating instructions
## Custom instructions
{ANCHOR}

Rules:
- "Operating instructions": careful, methodical; when a task gives an exact function signature,
  output format, or rule set, follow it LITERALLY; clean dependency-free Python (stdlib only);
  read the entire task before coding; no explanations unless asked.
- Keep competency guidance TERSE — role + skills, minimal tactics. (A refinement pass adds
  tactical guidance later; a terse v0 is intentional.)
- The final section "{ANCHOR}" must contain only the line: _None yet._
Return ONLY the markdown."""
    spec = kimi_chat([{"role": "user", "content": prompt}], temperature=0.3)
    if ANCHOR not in spec:                       # the anchor is load-bearing for refine
        spec = spec.rstrip() + f"\n\n{ANCHOR}\n\n_None yet._\n"
    return spec
```
*Anchor discipline (§0.5 bug 4): everything below `{ANCHOR}` belongs to `refine.py`. Any future section must be inserted BEFORE the anchor, never after.*

### Task 12: `run.py` (orchestrator, seed-battery path)

**Files:** Create: `run.py`

- [ ] **Step 1:** Write `run.py`:
```python
import argparse, json
import concurrent.futures as cf

import framework, gap
from agent import generate_agent, ANCHOR
from assess import assess_skill
from config import make_daytona, MAX_PARALLEL, TARGET_COVERAGE, MAX_ROUNDS

def load_seed_battery(role_codes):
    items = json.load(open("data/battery_seed.json"))
    kept = [i for i in items if i["code"] in role_codes]   # §0.5 bug 1: seed ∩ role's own codes
    if not kept:
        raise SystemExit("Seed battery shares no skill codes with this role — "
                         "refusing to fabricate a score.")
    return kept

def assess_round(daytona, battery, spec, label):
    with cf.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:   # one sandbox per skill
        results = list(ex.map(lambda it: assess_skill(daytona, it, spec), battery))
    report = gap.gap_report(results)
    gap.print_report(report, label)
    return report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default=framework.DEMO_ROLE)
    ap.add_argument("--task", help="Mode B: free-text task (stretch)")
    ap.add_argument("--generate", action="store_true",
                    help="use Kimi-generated grounded battery instead of the seed")
    ap.add_argument("--instructions", default="",
                    help="requester's custom instructions")
    args = ap.parse_args()

    role_query = args.role
    if args.task:                                          # stretch C (Phase 10)
        import classify
        recs = classify.recommend_roles(args.task)
        role_query = recs[0]["role"]
        print(f"Task classified -> {role_query} "
              f"(confidence {recs[0]['confidence']}, matched on {recs[0]['matched_on']})")

    sector, track, role = framework.resolve_role(role_query)
    print(f"Role: {role}  [{sector} / {track}]  — source: SkillsFuture SFw")
    skills = framework.get_skills(role)
    context = framework.get_context(role)
    executable = framework.select_executable(skills)
    print(f"{len(skills)} official skills; {len(executable)} executable "
          f"(rest are rubric-track)")

    digest = None
    try:                                                   # §10: forced-visible, up front
        import oxylabs_fetch
        digest = oxylabs_fetch.fetch_task_material(role)
    except ImportError:
        pass

    if args.generate:                                      # stretch A (Phase 8)
        import battery as batt
        bat = batt.build_battery(role, executable, context, digest)
    else:
        bat = load_seed_battery({s["code"] for s in skills})
    print(f"Battery: {len(bat)} graded tasks")

    spec = generate_agent(role, skills, context, args.instructions, sector)
    if ANCHOR not in spec or len(spec) < 400:              # §8 fallback: agent_seed.md
        print("agent-gen fallback -> data/agent_seed.md")
        spec = open("data/agent_seed.md").read()
    open("agent_v0.md", "w").write(spec)

    daytona = make_daytona()
    rounds = [assess_round(daytona, bat, spec, "baseline (spec v0)")]

    import refine
    rnd = 0
    while (rounds[-1]["coverage_pct"] < TARGET_COVERAGE
           and rnd < MAX_ROUNDS - 1 and rounds[-1]["gaps"]):
        rnd += 1
        print(f"\nRefining spec (round {rnd}) — patching '{ANCHOR}' ...")
        spec = refine.patch_agent(spec, rounds[-1]["gaps"])
        rounds.append(assess_round(daytona, bat, spec, f"refined (spec v{rnd})"))

    open("agent_final.md", "w").write(spec)
    out = {"role": role, "sector": sector, "track": track,
           "rounds": [{"coverage_pct": r["coverage_pct"],
                       "results": [{k: v for k, v in res.items() if k != "task_prompt"}
                                   for res in r["results"]]}
                      for r in rounds]}
    json.dump(out, open("output.json", "w"), indent=2)
    print("\nSaved: agent_final.md · output.json · events.jsonl")

if __name__ == "__main__":
    main()
```
- [ ] **Step 2:** Create a stub `refine.py` so the import resolves (filled in Phase 5):
```python
def patch_agent(agent_spec, gaps):
    return agent_spec        # placeholder until Phase 5 — refine loop is a no-op
```
- [ ] **Step 3:** Run: `python run.py` — Expected: role header, `3` seed tasks kept (all three seed codes are in the role's 14 — verified), spec v0 generated (or seed fallback message), three parallel sandbox lifecycles interleaved, a baseline report table with a headline `%`, and the three output files. If Daytona rate-limits: `MAX_PARALLEL=2 python run.py`.
- [ ] **Step 4:** `git add -A && git commit -m "feat: run.py parallel baseline over seed battery"`

---

## Phase 5 — (~13:20–14:00): `refine.py` → the lift

### Task 13: real `refine.py`

**Files:** Modify: `refine.py` (replace the stub)

- [ ] **Step 1:** Write `refine.py`:
```python
from config import kimi_chat
from agent import ANCHOR

def make_injection(gap_item):
    """One concrete, reusable instruction to pass this CLASS of task (the v1 coach prompt)."""
    prompt = f"""An AI agent scored {gap_item['score']:.2f} (needs >= 0.7) on this graded task:

---
{gap_item['task_prompt'][:3000]}
---

Write ONE concrete, reusable instruction (2-4 sentences, imperative voice) that would help the
agent pass tasks OF THIS KIND. Focus on likely failure modes: exact output formats, applying
rules in the stated order, edge cases (missing values, duplicates, case sensitivity, rounding,
tie-breaks, sort stability). Do not mention this specific task's data or test values.
Return only the instruction text."""
    return kimi_chat([{"role": "user", "content": prompt}], temperature=0.3).strip()

def patch_agent(agent_spec, gaps):
    """Append guidance under the anchor. Everything after the anchor is refine's territory
    (§0.5 bug 4) — other sections must never be inserted below it."""
    if ANCHOR not in agent_spec:
        agent_spec = agent_spec.rstrip() + f"\n\n{ANCHOR}\n\n_None yet._\n"
    head, _, tail = agent_spec.partition(ANCHOR)
    tail = tail.replace("_None yet._", "").rstrip()
    for g in gaps:
        tail += (f"\n\n### {g['skill']} (required L{g['required_level']})\n"
                 f"{make_injection(g)}")
    return head + ANCHOR + tail + "\n"
```
- [ ] **Step 2:** Add anchor-safety checks to `test_local.py` (append):
```python
from agent import ANCHOR
import refine
spec = f"# X\n\nbody\n\n{ANCHOR}\n\n_None yet._\n"
p1 = refine.patch_agent(spec, [])            # no gaps -> no Kimi call
assert ANCHOR in p1 and "_None yet._" not in p1
p2 = refine.patch_agent(p1 + "\n### Old guidance\nkeep me", [])
assert "keep me" in p2                       # a second pass never destroys prior guidance
print("ANCHOR TESTS OK")
```
Run: `python test_local.py` — Expected: `LOCAL TESTS OK` and `ANCHOR TESTS OK`.
- [ ] **Step 3:** Run the full loop: `python run.py` — Expected: baseline report → `Refining spec (round 1)` → second report with a **higher** coverage number, and `agent_final.md` now contains populated `## Learned skill guidance` subsections. Production says one round gives visible lift. *Demo-drama lever (§4): if v0 already scores too high, terser v0 competency lines widen the honest gap.*
- [ ] **Step 4:** `git add -A && git commit -m "feat: refine loop — patched spec shows real lift"`

---

## Phase 6 — (~14:00–14:30): `dashboard.py`

### Task 14: `dashboard.py`

**Files:** Create: `dashboard.py`

- [ ] **Step 1:** Write `dashboard.py` (real-data page; `data/dashboard_mockup.html` remains the polished stage visual for the theatre beat):
```python
import json, html, pathlib, webbrowser

def bar(pct, color):
    return ('<span style="display:inline-block;background:#1d2735;border-radius:4px;'
            'width:260px;height:14px;vertical-align:middle">'
            f'<span style="display:block;width:{max(2, pct):.0f}%;height:14px;'
            f'border-radius:4px;background:{color}"></span></span>')

def main():
    out = json.load(open("output.json"))
    spec = pathlib.Path("agent_final.md").read_text()
    first, last = out["rounds"][0], out["rounds"][-1]
    rows = ""
    for b, f in zip(first["results"], last["results"]):
        rows += (f"<tr><td style='padding:6px 14px'>{html.escape(b['skill'])} "
                 f"(L{b['required_level']})</td>"
                 f"<td>{bar(b['score']*100, '#ef5350')} {b['score']*100:.0f}%</td>"
                 f"<td>{bar(f['score']*100, '#28c76f')} {f['score']*100:.0f}%</td></tr>")
    rub = out.get("rubric")
    rub_html = ""
    if rub:
        rub_html = (f"<p style='color:#f5a623'>&#9675; Rubric-assessed: "
                    f"<b>{rub['covered']}/{rub['total']}</b> K&amp;A items ({rub['pct']}%) "
                    f"&mdash; <em>NOT execution-verified · never blended</em></p>")
    page = f"""<!doctype html><meta charset="utf-8"><title>AgentProof</title>
<body style="font-family:-apple-system,sans-serif;background:#0a0e13;color:#e8eef5;padding:32px">
<h1 style="margin:0">{html.escape(out['role'])}</h1>
<p style="color:#8b98a7">{html.escape(out['sector'])} · {html.escape(out['track'])} ·
source: SkillsFuture Skills Framework</p>
<h2 style="color:#28c76f">&#10004; Executed: {first['coverage_pct']}% &rarr;
{last['coverage_pct']}% role-readiness</h2>
{rub_html}
<table style="border-collapse:collapse"><tr style="color:#8b98a7;text-align:left">
<th style="padding:6px 14px">skill</th><th>baseline</th><th>refined</th></tr>{rows}</table>
<p style="color:#8b98a7;margin-top:18px">Every green bar = code THIS agent wrote that ran
in a Daytona sandbox.</p>
<h2>Delivered agent spec (agent_final.md)</h2>
<pre style="background:#111823;border:1px solid #1d2735;border-radius:10px;padding:18px;
white-space:pre-wrap;font-size:12.5px">{html.escape(spec)}</pre></body>"""
    pathlib.Path("dashboard.html").write_text(page)
    webbrowser.open("file://" + str(pathlib.Path("dashboard.html").resolve()))
    print("dashboard.html opened")

if __name__ == "__main__":
    main()
```
- [ ] **Step 2:** Run: `python dashboard.py` — Expected: browser opens with red→green bars per skill, the headline `X% → Y%`, and the delivered spec below. Verify the **served page content**, not just the exit code (§0.5 bug 7).
- [ ] **Step 3:** `git add -A && git commit -m "feat: dashboard — before/after bars + delivered spec"`

**✅ CHECKPOINT (~14:30): the guide's steps 1–6 are green — a complete, honest, rehearsable demo exists. Everything after this line makes it better, not possible.**

---

## Phase 7 — (~14:30–15:30): the two-track scorecard (CORE — the differentiator, §0.5)

### Task 15: `battery.py::rubric_score` + run/dashboard integration

**Files:** Create: `battery.py` (rubric part only — generation comes in Phase 8) · Modify: `run.py`, `dashboard.py` (already rubric-aware)

- [ ] **Step 1:** Write `battery.py` with the judge:
```python
from config import kimi_json

def rubric_score(skill, level, ka, agent_spec):
    """LLM-as-judge over the skill's OWN K&A checklist. Honest, framework-anchored,
    and NEVER blended with executed bars. Judge reads the FULL spec (§0.5 bug 2);
    K&A items are already atomic + artifact-assessable (§0.5 bug 3)."""
    items = ka["items"]
    if not items:
        return None
    listing = "\n".join(f"{i+1}. [{it['kind']}] {it['item']}"
                        for i, it in enumerate(items))
    prompt = f"""You are grading an AI agent SPECIFICATION document against Singapore's
SkillsFuture Knowledge & Ability checklist for the skill "{skill}" at level L{level}
("{ka['proficiency_description']}").

Checklist — {len(items)} atomic items, judge each independently:
{listing}

Agent specification (FULL text — judge only what this document contains, never
operational process such as uptime, sign-offs, or audit trails):
---
{agent_spec}
---

For each item, covered=true only if the specification's content would plausibly direct the
agent to demonstrate that item.
Return STRICT JSON: {{"covered": [true or false, exactly {len(items)} entries, in order]}}"""
    obj = kimi_json([{"role": "user", "content": prompt}], temperature=0.0,
                    validate=lambda o: (isinstance(o.get("covered"), list)
                                        and len(o["covered"]) == len(items)),
                    max_tokens=2048)
    n = sum(1 for c in obj["covered"] if c)
    return {"skill": skill, "level": level, "covered": n, "total": len(items),
            "pct": round(100 * n / len(items), 1), "execution_verified": False}
```
- [ ] **Step 2:** In `run.py`, after `open("agent_final.md", "w").write(spec)` and **before** building `out`, add:
```python
    from battery import rubric_score
    exec_codes = {s["code"] for s in executable}
    rubric_skills, cov, tot = [], 0, 0
    for s in [x for x in skills if x["code"] not in exec_codes][:6]:   # cap for time
        ka = framework.get_ka(s["code"], s["required_level"])
        r = rubric_score(s["skill"], s["required_level"], ka, spec)
        if r:
            rubric_skills.append(r)
            cov += r["covered"]; tot += r["total"]
    rubric = ({"skills": rubric_skills, "covered": cov, "total": tot,
               "pct": round(100 * cov / tot, 1)} if tot else None)
```
then add `"rubric": rubric,` to the `out` dict, and after the save print the two-track headline:
```python
    if rubric:
        print(f"\nHEADLINE — Executed: {rounds[-1]['coverage_pct']}% of level-points "
              f"· Rubric: {rubric['covered']}/{rubric['total']} K&A items "
              f"({rubric['pct']}%) — two numbers, NEVER blended")
```
- [ ] **Step 3:** Run: `python run.py` — Expected: after the refine round, ~6 rubric skills judged (temp 0.0), and the two-number headline. Then `python dashboard.py` — the amber `○ Rubric-assessed … NOT execution-verified` line now renders (the Phase-6 code already handles it).
- [ ] **Step 4:** Sanity-check honesty: the executed % and rubric % must appear **nowhere combined**. `grep -n "blend\|overall" run.py dashboard.py` — no blended aggregate exists.
- [ ] **Step 5:** `git add -A && git commit -m "feat: two-track scorecard — executed vs rubric, never blended"`

---

## Phase 8 — STRETCH A (~15:30+, only if 1–7 green): Kimi-generated grounded battery

### Task 16: `battery.py::generate_skill` + `build_battery` (with self-validation)

**Files:** Modify: `battery.py`

- [ ] **Step 1:** Add to `battery.py`:
```python
import json
from config import kimi_json, make_daytona, SANDBOX_TIMEOUT
import framework
from assess import parse_grade

_REQ_KEYS = {"task_prompt", "grader_code", "reference_code"}

def generate_skill(role, skill, required_level, context, ka, task_material):
    ka_lines = "\n".join(f"- [{it['kind']}] {it['item']}" for it in ka["items"][:10])
    key_tasks = "; ".join(kt for c in context["critical_work_functions"][:3]
                          for kt in c["key_tasks"][:2])
    prompt = f"""Generate ONE graded coding assessment for an AI agent.

Role: {role}
Skill: {skill} — required proficiency level L{required_level} (SkillsFuture framework)
Official proficiency description: {ka['proficiency_description']}
Official Knowledge & Ability items — the task MUST exercise 2-4 of these, faithfully:
{ka_lines}
Role key tasks (flavour): {key_tasks}
Current market demand from live postings (optional flavour): {task_material or '(none)'}

Return STRICT JSON with exactly these keys:
- "task_prompt": self-contained task asking for a Python function `def solve(...)`.
  Pure stdlib, deterministic, no I/O or network. Spell out exact rules, rule ORDER,
  edge cases, and output format precisely.
- "grader_code": self-contained Python defining test cases; it calls solve(...) which is
  already defined in the same namespace (do NOT import or redefine solve); wraps every call
  in try/except; and its LAST printed line is exactly:
  GRADE:{{"score": <fraction of cases passed, 0..1>}}
- "reference_code": a correct reference implementation of solve(...).
Task must be solvable in <=60 lines and graded purely by running the code."""
    return kimi_json([{"role": "user", "content": prompt}], temperature=0.2,
                     validate=lambda o: _REQ_KEYS <= set(o), max_tokens=4096)

def _validate_item(daytona, item):
    """§0.5 bug 5: static check, then run the item's own reference against its grader in a
    sandbox — an item its own reference can't solve is a bad item; drop it."""
    try:
        compile(item["grader_code"], "<grader>", "exec")
        compile(item["reference_code"], "<ref>", "exec")
    except SyntaxError:
        return False
    sb = daytona.create()
    try:
        resp = sb.process.code_run(item["reference_code"] + "\n\n" + item["grader_code"],
                                   timeout=SANDBOX_TIMEOUT)
        score, _ = parse_grade(getattr(resp, "result", ""))
        return score >= 0.99
    except Exception:
        return False
    finally:
        try:
            sb.delete()
        except Exception:
            pass

def build_battery(role, executable, context, task_material=None):
    daytona = make_daytona()
    out = []
    for s in executable:
        ka = framework.get_ka(s["code"], s["required_level"])
        try:
            gen = generate_skill(role, s["skill"], s["required_level"],
                                 context, ka, task_material)
            item = {"skill": s["skill"], "code": s["code"],
                    "required_level": s["required_level"],
                    "task_prompt": gen["task_prompt"],
                    "grader_code": gen["grader_code"]}
            if _validate_item(daytona, {**item, "reference_code": gen["reference_code"]}):
                out.append(item)
                print(f"battery: generated + self-validated '{s['skill']}' L{s['required_level']}")
            else:
                print(f"battery: DROPPED invalid item for '{s['skill']}'")
        except Exception as e:
            print(f"battery: generation failed for '{s['skill']}': {e}")
    if not out:                                            # §8: never dead in the water
        print("battery: nothing survived — falling back to data/battery_seed.json")
        seed = json.load(open("data/battery_seed.json"))
        role_codes = {s["code"] for s in framework.get_skills(role)}
        out = [i for i in seed if i["code"] in role_codes]
    return out
```
- [ ] **Step 2:** Run: `python run.py --generate` — Expected: 5 skills generated (some may drop on self-validation — that's the guardrail working), full loop over the grounded battery, lift, two-track headline.
- [ ] **Step 3:** `git add -A && git commit -m "feat: K&A-grounded generated battery with sandbox self-validation"`

---

## Phase 9 — STRETCH B: `oxylabs_fetch.py` (forced-visible probe)

*Note: the §10 sponsor rule makes the one visible call effectively required for scoring if you have booth keys — build `fetch_task_material` first, salary/career-map only if time allows.*

### Task 17: `oxylabs_fetch.py`

**Files:** Create: `oxylabs_fetch.py`

- [ ] **Step 1:** Write `oxylabs_fetch.py`:
```python
import os, requests

def _query(payload):
    user, pwd = os.getenv("OXYLABS_USERNAME"), os.getenv("OXYLABS_PASSWORD")
    if not user or not pwd:
        return None
    r = requests.post("https://realtime.oxylabs.io/v1/queries",
                      auth=(user, pwd), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_task_material(role_title):
    """Live postings digest. NEVER raises (§8); prints the §10 forced-visible line on success."""
    try:
        data = _query({"source": "google_search", "parse": True,
                       "query": f'"{role_title}" job Singapore responsibilities'})
        if not data:
            return None
        organic = data["results"][0]["content"]["results"]["organic"][:5]
        digest = "\n".join(f"- {r.get('title', '')}: {str(r.get('desc', ''))[:200]}"
                           for r in organic)
        print(f"Oxylabs: pulled {len(organic)} current postings for {role_title}")
        return digest or None
    except Exception as e:
        print(f"Oxylabs unavailable ({type(e).__name__}) — proceeding without seasoning")
        return None

def fetch_salary_band(role_title):
    """Salary range for Screen-4 market strip. Optional; None -> UI omits the band."""
    try:
        data = _query({"source": "google_search", "parse": True,
                       "query": f'"{role_title}" salary Singapore MyCareersFuture'})
        if not data:
            return None
        snippets = " ".join(str(r.get("desc", ""))
                            for r in data["results"][0]["content"]["results"]["organic"][:5])
        import re
        amts = [int(a.replace(",", "")) for a in
                re.findall(r"\$\s?([\d,]{4,7})", snippets)][:8]
        if len(amts) < 2:
            return None
        return {"low": min(amts), "high": max(amts), "currency": "SGD",
                "source": "live search"}
    except Exception:
        return None
```
*(`fetch_career_map` is a further stretch: same `_query` shape against the SkillsFuture role page; skip unless everything else is done — its payoff is one dashboard strip.)*
- [ ] **Step 2:** `run.py` already calls `fetch_task_material` up front (Phase 4) — verify the visible line prints: `python run.py --generate` → `Oxylabs: pulled N current postings for …` appears **before** the pipeline. Save two real result snippets somewhere visible in case judges ask (§3).
- [ ] **Step 3:** `git add -A && git commit -m "feat: oxylabs live postings digest, forced-visible"`

---

## Phase 10 — STRETCH C: `classify.py` (Mode B, task-first entry)

### Task 18: `classify.py`

**Files:** Create: `classify.py`

- [ ] **Step 1:** Write `classify.py`:
```python
import re
from collections import Counter
import framework
from config import kimi_json

_STOP = set(("a an the and or of to for in on with is are be this that it i we my our need "
             "want into from by as at").split())

def _tokens(text):
    return [w for w in re.findall(r"[a-z]+", (text or "").lower())
            if w not in _STOP and len(w) > 2]

def recommend_roles(task, k=3):
    """Retrieve-and-rank over REAL roles only (keyword overlap -> Kimi choosing among the
    top-10). Kimi never names a role from scratch — the guardrail the proof depends on."""
    q = Counter(_tokens(task))
    scored = []
    for sector, _trk, role, desc, _perf in framework._sheet("Job Role_Description"):
        if not role:
            continue
        doc = Counter(_tokens(f"{role} {desc or ''}"))
        overlap = sum(min(q[w], doc[w]) for w in q)
        if overlap:
            scored.append((overlap, role, sector))
    top = sorted(scored, reverse=True)[:10]
    if not top:
        raise SystemExit("Task matched no dataset role — rephrase, or use --role (Mode A).")
    listing = "\n".join(f"{i+1}. {r}  ({s})" for i, (_, r, s) in enumerate(top))
    obj = kimi_json([{"role": "user", "content":
        f'A user describes this task: "{task}"\n\n'
        f"Which of these REAL SkillsFuture roles fit best? Choose ONLY from this list:\n"
        f"{listing}\n\n"
        f'Return STRICT JSON: {{"picks": [{{"n": <list number>, "confidence": <0-100>, '
        f'"matched_on": ["duty", ...]}}]}} — best {k} picks, best first.'}],
        temperature=0.2,
        validate=lambda o: isinstance(o.get("picks"), list) and o["picks"])
    out = []
    for p in obj["picks"][:k]:
        _, role, sector = top[int(p["n"]) - 1]
        out.append({"role": role, "sector": sector,
                    "confidence": p.get("confidence", 0),
                    "matched_on": p.get("matched_on", [])})
    return out
```
- [ ] **Step 2:** Run (guide §6 step 7): `python run.py --generate --task "review vendor NDAs and pull payment terms into a comparison"` — Expected: `Task classified -> <a real dataset role>` then the normal loop. **Demo cap: it resolves to ONE role** (`run.py` takes `recs[0]` only). For the stage, steer any Mode-B demo task toward an executable role so proof bars stay honest.
- [ ] **Step 3:** `git add -A && git commit -m "feat: Mode B — task classifies to real roles, retrieval not invention"`

---

## Phase 11 — LAST STRETCH (only after everything is green AND rehearsed): theatre

### Task 19: events replay visual

- [ ] **Step 1:** `events.jsonl` already records the real lifecycle (Phase 3). Wire `data/dashboard_mockup.html` as the stage visual: its per-sandbox cards (`#sb0…`, `#tm0…`, `#st0…`) and sponsor chips are driven by an internal script with demo data — replace that script's hardcoded event array with the contents of `events.jsonl` (paste-in or a `fetch('events.jsonl')` if served via `python -m http.server`). Timebox: **30 minutes**; if it fights back, drop it — the terminal's live `[spawn]…[teardown]` lines are already a real, watchable stream.
- [ ] **Step 2:** If done: `git add -A && git commit -m "feat: theatre replay of real lifecycle events"`

---

## Phase 12 — (~16:30+): demo prep & the 60-second pre-demo pass

### Task 20: rehearse the 2-minute script (§7) and the §7.5 seam beat

- [ ] **Step 1:** Run the demo flow twice end-to-end exactly as scripted: problem → request (SkillsFuture list + Oxylabs line) → generated spec → `python run.py` (name **Daytona/Kimi/Oxylabs aloud as each fires**) → lift → `python dashboard.py` money shot → honesty close (two tracks, never blended) → §7.5 receipts beat.
- [ ] **Step 2:** Seam-beat discipline (§7.5): say "**exercised** in real sandboxes, **rubric-assessed**, never blended into executed"; never say "the agents talk to each other" or "this ran today". Receipts tabs open (Phase 0 Step 5).
- [ ] **Step 3:** The 60-second pre-demo pass (§10), in order:
  1. `python smoke_test.py` green ☐
  2. Oxylabs forced probe printed a real "pulled N postings" line ☐ (if red: drop the "live" claim, show saved snippets)
  3. `python run.py` produced baseline **and** lifted final; `agent_final.md` exists ☐
  4. `python dashboard.py` opens red→green bars + headline ☐
  5. Nosana line (A or B) decided and rehearsed ☐

---

## Risks & fallbacks (operative during the build — from §8)

| Break | Response |
|---|---|
| Daytona quota / rate limit | `MAX_PARALLEL=1 python run.py` (env-tunable, no code change) |
| `daytona` import fails | `pip install daytona-sdk` — `make_daytona()` tries both names |
| Kimi battery-gen bad JSON | `kimi_json` retries ×2, `_validate_item` drops bad items, `build_battery` falls back to seed |
| Agent-gen junk | `run.py` falls back to `data/agent_seed.md` (checks anchor + length) |
| Oxylabs down / no key | `fetch_task_material` returns `None`, prints why; pipeline unaffected |
| Grader crashes on candidate code | Graders wrap calls in try/except; `parse_grade` → 0 + note |
| Suspicious 100% on seed | The `seed ∩ role codes` filter refused-to-fabricate path in `load_seed_battery` |
| Judge marks obvious items unmet | Full-spec window is passed (no truncation); K&A items are atomic by construction |
| No internet | Only Daytona + Kimi need network; xlsx + seeds are local |

## Verification (end-to-end)

1. `python test_local.py` → `LOCAL TESTS OK` + `ANCHOR TESTS OK` (offline).
2. `python smoke_test.py` → both APIs green.
3. Data layer probe (Phase 2 Step 2) → 14 skills / 5 executable / K&A items present.
4. `python run.py` (seed path) → baseline report, refine round, higher coverage, `agent_v0.md` vs `agent_final.md` differ only below the `## Learned skill guidance` anchor, `output.json` + `events.jsonl` written.
5. `python run.py --generate` → grounded battery (drops announced), two-track headline prints two numbers and no blended figure anywhere.
6. `python dashboard.py` → open the served page and visually confirm bars, headline, rubric badge, and the spec text (never trust the exit code alone).

## Execution options

**1. Subagent-driven (recommended):** dispatch a fresh subagent per task with this plan, review between tasks (`superpowers:subagent-driven-development`).
**2. Inline:** execute task-by-task in-session with checkpoints (`superpowers:executing-plans`).

Note: per BUILD_GUIDE §0.5 the day-of code should be **built fresh at the hackathon** — if today's session is prep only, execute Phase 0 now and carry this plan (printed/on-screen) into Saturday.