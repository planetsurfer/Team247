# Loop prompt: remove Daytona + Oxylabs

> **Usage:** `/loop Read LOOP_REMOVE_SPONSORS.md and execute the refactor it describes, task by task, then run the VERIFY steps. Stop when all verify checks pass.`

---

Refactor the AgentProof project at /Users/matthew/Documents/Team247 to REMOVE Daytona and Oxylabs entirely. The LLM layer is already on Alibaba ModelStudio (done — `.env` has `LLM_API_KEY` + `LLM_MODEL_*` per-function routing; do not touch the LLM layer). Replace Daytona's sandboxed code execution with local subprocess execution; delete Oxylabs.

## BACKGROUND (verified)

- Daytona's used API surface is ONLY: `make_daytona()` → runner; `runner.create()` → sandbox; `sandbox.id`; `sandbox.process.code_run(code, timeout=...)` → `resp.result` (stdout string); `sandbox.delete()` / `runner.delete(sb)` fallback. Callers read ONLY stdout via `getattr(resp,"result","")`. The harness (`assess.parse_grade`) scans stdout bottom-up for a final line `GRADE:{"score": <float 0..1>}` and clamps to `[0,1]`; no GRADE line → `(0.0, "no GRADE line in output")`. No filesystem/stdin/env injection is used. So a drop-in local runner with the SAME 4-method surface needs ZERO logic changes to `assess.py`/`battery.py`/`run.py`/`assess_one.py` — only an import rename.
- `make_daytona()` is in `config.py` (~lines 71-76): `from daytona import Daytona, DaytonaConfig; return Daytona(DaytonaConfig(api_key=os.environ["DAYTONA_API_KEY"]))` (raises KeyError without the key). `SANDBOX_TIMEOUT` (default 20) is threaded into every `code_run`.
- `code_run` is called WITH `timeout=` in `assess.py:39` and `battery.py:82`, and WITHOUT `timeout` in `smoke_test.py:9` — so the replacement's `timeout` must be OPTIONAL.
- Oxylabs already has a clean None fallback: `oxylabs_fetch.fetch_task_material` returns None on missing creds → `battery.py:55` renders `"(none)"`; `fetch_salary_band` → None → dashboard omits the band. `build_battery`'s `task_material` param defaults to None.
- Oxylabs call sites: `run.py:52-57` (`try/import oxylabs_fetch` → `digest` → `build_battery(...,digest)`); `dashboard.py:42-50` (`try` → `fetch_salary_band` → `salary`). `scrape_enrich.py` is INDEPENDENT (no Oxylabs; hits public MyCareersFuture API, writes `data/role_enrichment.json` read by `dashboard.py:62`) — LEAVE IT.

## TASKS

### 1) config.py — drop-in LocalRunner, renamed make_runner

Keep `SANDBOX_TIMEOUT` and the `certifi` block. Remove `from daytona import ...` and the `DAYTONA_API_KEY` lookup (local runner needs no key). Add this:

```python
import subprocess, sys, tempfile, uuid, shutil

class _LocalResp:
    __slots__ = ("result",)
    def __init__(self, result): self.result = result

class _LocalProcess:
    def __init__(self, workdir): self._dir = workdir
    def code_run(self, code, timeout=None):
        script = os.path.join(self._dir, "run.py")
        with open(script, "w") as f: f.write(code)
        try:
            cp = subprocess.run([sys.executable, "run.py"], cwd=self._dir,
                                capture_output=True, text=True, timeout=timeout)
            return _LocalResp(cp.stdout or "")
        except subprocess.TimeoutExpired as e:
            out = e.stdout if isinstance(e.stdout, str) else ""
            return _LocalResp(out or "")
        except Exception:
            return _LocalResp("")

class _LocalSandbox:
    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self._dir = tempfile.mkdtemp(prefix="ap_run_")
        self.process = _LocalProcess(self._dir)
    def delete(self):
        shutil.rmtree(self._dir, ignore_errors=True)

class LocalRunner:
    def create(self): return _LocalSandbox()
    def delete(self, sb):
        if sb is not None: sb.delete()

def make_runner():
    return LocalRunner()
```

### 2) Call-site import rename (mechanical, NO logic changes)

- `run.py:7` → `from config import make_runner, MAX_PARALLEL, TARGET_COVERAGE, MAX_ROUNDS`; rename var `daytona`→`runner` at `run.py:74,75,84` and the `assess_round` param at `run.py:17,19`.
- `assess.py:27` → rename `assess_skill(daytona, item, spec)` param to `runner` (used at the `runner.create()`, `code_run`, `runner.delete()` calls inside).
- `battery.py:2` → `from config import llm_json, make_runner, SANDBOX_TIMEOUT`; line 94 `daytona = make_runner()` (rename var to `runner`; `_validate_item` calls `runner.create()`).
- `assess_one.py:4,10` → `make_runner()`.
- `smoke_test.py:1` → `from config import llm_chat, make_runner`; rewrite lines 6-17 sandbox check to:

```python
r = make_runner()
sb = r.create()
try:
    resp = sb.process.code_run("print(2+2)")
    out = (getattr(resp, "result", "") or "").strip()
    assert "4" in out, f"local exec stdout was {out!r}"
    print(f"LOCAL EXEC: OK (2+2={out} | sandbox {sb.id})")
finally:
    sb.delete()
print("SMOKE OK")
```

Keep the LLM ping at the top unchanged.

### 3) Oxylabs — hard removal

- DELETE `/Users/matthew/Documents/Team247/oxylabs_fetch.py`.
- `run.py:52-57` → delete the `try/import oxylabs_fetch/fetch_task_material` block. `digest` stays None; call `build_battery(role, executable, context, None)` (`task_material` defaults None → renders `"(none)"`).
- `dashboard.py:42-50` → delete the `try/import oxylabs_fetch/fetch_salary_band` block. `salary` stays None; UI omits the band.
- Leave `scrape_enrich.py` untouched.

### 4) Cosmetic + packaging

- `dashboard.py:28` `"graded in Daytona sandbox ..."` → `"graded via local subprocess ..."`.
- `teamspec.py:97` prose `"...isolated Daytona sandboxes..."` → `"...local subprocesses..."`.
- `requirements.txt`: drop the `daytona` line (keep `openai`, `python-dotenv`, `requests`, `openpyxl`, `certifi`).
- `.env` and `.env.example`: remove `DAYTONA_API_KEY=`, `OXYLABS_USERNAME=`, `OXYLABS_PASSWORD=` lines (and the now-empty "Non-LLM sponsor infra" header). Leave all `LLM_*` rows untouched.

## VERIFY (after edits)

- `python -c "import config; r=config.make_runner(); sb=r.create(); print(sb.process.code_run('print(2+2)').result); sb.delete()"` → prints `4`.
- `python3 -m py_compile *.py` → all clean.
- `python smoke_test.py` → `LLM: OK`, `LOCAL EXEC: OK`, `SMOKE OK` (uses the user's `LLM_API_KEY` already in `.env`).
- `python assess_one.py 0` → full single-skill path through Alibaba LLM + local code execution, prints `GRADE:{"score": ...}`.
- `grep -rni "make_daytona\|import daytona\|oxylabs" *.py` → no matches; `grep -rni "daytona" *.py` → only the cosmetic rebrand lines or none.
- `git status` → `oxylabs_fetch.py` deleted; `config.py`, `run.py`, `assess.py`, `battery.py`, `assess_one.py`, `smoke_test.py`, `dashboard.py`, `teamspec.py`, `requirements.txt`, `.env.example` modified; `.env` `LLM_*` block unchanged.

## RISK (acknowledged, do not block)

Local subprocess is NOT a true sandbox — LLM-generated candidate/grader Python runs as the host user. Mitigation = per-sandbox temp working dir cleaned on delete. The grader prompt asks for stdlib/no-network at prompt level (not enforced). Acceptable for a dev box; user explicitly chose to drop Daytona. Use ScheduleWakeup to self-pace; stop when all tasks + verification pass.
