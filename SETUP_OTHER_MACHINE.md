# Setup on another machine

The repo is designed to be reproducible, but the **runtime DB and function index are gitignored** — a folder copy brings them along, while the platform-specific bits (`.venv`, `node_modules`) must be rebuilt on the new machine.

## 1. Rebuild the platform-specific bits

Skip if you didn't copy `.venv` / `node_modules`; mandatory if the new machine is a different OS/arch.

```bash
# from the copied folder root
rm -rf .venv __pycache__ web/node_modules web/tsconfig.tsbuildinfo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
```

If you're on the **same OS/arch** as the source machine (macOS ARM) and you *did* copy `.venv` + `node_modules`, you can skip the deletions — but if anything fails to import, do the rebuild above. It's the safe default.

## 2. Sanity-check the DB came along (the valuable part)

```bash
ls -lh data/agentproof.db        # expect ~80M
python -c "import sqlite3; c=sqlite3.connect('data/agentproof.db'); print('roles:', c.execute('select count(*) from roles').fetchone()[0]); print('role_functions:', c.execute('select count(*) from role_functions').fetchone()[0])"
```

Expected: `roles: 1910`, and `role_functions:` non-zero (that's the function index that holds the 3.38 retrieval). If `role_functions` is 0 or the table is missing, run:

```bash
python build_function_index.py   # LLM calls, a few minutes
```

## 3. Confirm `.env` has real values

```bash
grep -E '^(LLM_API_KEY|APP_ADMIN_TOKEN)=' .env
```

`LLM_API_KEY` must be filled. If `APP_ADMIN_TOKEN` is missing, add `APP_ADMIN_TOKEN=localdev`.

## 4. Boot + smoke

```bash
PYTHONPATH=. uvicorn app.main:app
```

In another terminal:

```bash
curl -s http://127.0.0.1:8000/api/catalog?q=account | head -c 200
curl -s -X POST http://127.0.0.1:8000/api/team/recommend \
  -H 'Content-Type: application/json' \
  -d '{"task":"prepare a quotation","artifacts_needed":[]}' | head -c 300
```

## 5. Tests (the 100%-team guarantee)

```bash
PYTHONPATH=. python -m pytest tests/test_chat_flow_corpus.py -v
```

## What's NOT in git (must rebuild if you ever clone instead of copy)

- **`data/agentproof.db`** — runtime DB, gitignored. Rebuild from the tracked xlsx:
  ```bash
  python -m app.seed_catalog --idempotent   # 0 LLM calls, deterministic
  python build_function_index.py            # rebuilds the role_functions index (LLM calls)
  ```
- **`.env`** — only `.env.example` is tracked; API key + admin token are yours to fill.
- **`web/node_modules`** — `npm install`.
- **`sim_results/`** — gitignored measurement data from prior runs; not needed to run.

## State of the work (don't re-litigate)

- Branch `feat/per-agent-specs-ka-guidance` carries the latest. Last commit: `25f8a98`.
- Current quality level: **3.38/5** at n=24 personas (function-index retrieval lift, +0.10 over the 2.87 v5 baseline). **Loop stopped by user decision** — the win is banked at n=24, NOT confirmed at 100-persona scale.
- **Diagnosed next lever (not started):** residual `missing_key_role` (44%) is no longer a retrieval problem (per-function recall = 100%, the right role is provably in the slate) — it's a **composer-selection** problem in `app/llm_contracts.team_recommend`.

## Known pre-existing test failures (predate this branch, don't fix as feature work)

- `tests/test_handoff_cycles.py` — uses `"from"`/`"to"` keys vs `validate_handoff_graph`'s `from_agent`/`to_agent`.
- `tests/test_intake_termination.py` — expects a `rounds` key intake results don't return.

## Sync to the correct repo

The repo this work belongs to is **`planetsurfer/Team247`** (`https://github.com/planetsurfer/Team247.git`). After a folder copy, the `.git/` directory comes along, so the `origin` remote should already point there — but **verify** before committing, because a folder copy can also bring a stale `upstream` remote (`eugenewong22/agentproof`) that you should NOT push to.

### Verify the remotes

```bash
git remote -v
```

You should see:

```
origin    https://github.com/planetsurfer/Team247.git (fetch)
origin    https://github.com/planetsurfer/Team247.git (push)
upstream  https://github.com/eugenewong22/agentproof.git (fetch)   # read-only, do NOT push here
upstream  https://github.com/eugenewong22/agentproof.git (push)
```

If `origin` is missing or wrong, fix it:

```bash
git remote set-url origin https://github.com/planetsurfer/Team247.git
```

If you do not want the `upstream` remote on this machine, remove it:

```bash
git remote remove upstream
```

### Authenticate with GitHub

If you cloned via folder copy rather than `git clone`, your local git may not have credentials cached for `planetsurfer/Team247`. Use the GitHub CLI (cleanest):

```bash
gh auth login              # follow the prompts; choose HTTPS + browser auth
gh auth setup-git          # configures git to use gh credentials for github.com
```

Or, if you prefer, use a Personal Access Token (classic) with `repo` scope as the password when you first push.

### Confirm the branch and sync

```bash
git branch -vv             # you should be on feat/per-agent-specs-ka-guidance, tracking origin
git status                 # working tree should be clean (or show only your intended changes)
git fetch origin
git pull --ff-only origin feat/per-agent-specs-ka-guidance   # bring in anything newer from origin
```

### Commit and push your work

```bash
git add <files>
git commit -m "<message>"
git push origin feat/per-agent-specs-ka-guidance
```

> **Push to `origin` only.** Never push to `upstream` — that points at the upstream `agentproof` repo you don't own.

### If you want a private mirror instead

For a private backup that ships the gitignored DB (the 80M catalog + function index) so you don't rebuild it on every machine, use Git LFS on a new private repo — see the conversation notes, or ask.

## Full narrative

`SESSION_LOG.md` — top entry dated 2026-07-19. Read that first; it's the densest handoff doc in the repo.
