"""Orchestration: drive personas through both tracks, enrich, judge, checkpoint.

One JSONL line per (persona, track), appended + flushed as soon as it
completes, so Ctrl-C loses at most the in-flight conversation and --resume
skips whatever is already on disk. A soft per-persona wall clock (checked
between HTTP calls) turns runaway conversations into status="timeout" records
instead of hanging the run.
"""
from __future__ import annotations

import datetime as dt
import json
import signal
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tests.simulation import judge as judge_mod
from tests.simulation import sim_user

MAX_INTAKE_ROUNDS = 8      # server force-terminates at 6; safety net
PERSONA_WALL_SECONDS = 480  # soft cap per conversation


class StopRun(Exception):
    """Raised between calls when SIGINT asked the run to wind down."""


class PersonaTimeout(Exception):
    pass


class _Clock:
    def __init__(self, budget: float, stop_flag: threading.Event):
        self.t0 = time.monotonic()
        self.budget = budget
        self.stop_flag = stop_flag

    def check(self):
        if self.stop_flag.is_set():
            raise StopRun()
        if time.monotonic() - self.t0 > self.budget:
            raise PersonaTimeout()

    def elapsed(self) -> float:
        return round(time.monotonic() - self.t0, 1)


# ── track drivers ───────────────────────────────────────────────────────────
def _enrich_team(client, body: dict, clock: _Clock) -> dict:
    """Resolve every agent against the catalog + card; attach judge context."""
    agents = []
    for a in body.get("agents") or []:
        clock.check()
        row = {k: a.get(k) for k in ("agent_id", "role_id", "role", "stage", "squad")}
        cat = client.get(f"/api/catalog/{a['role_id']}")
        row["catalog_found"] = cat.status_code == 200
        cat_body = cat.json() if cat.status_code == 200 else {}
        row["catalog_name_match"] = row["catalog_found"] and cat_body.get("role") == a.get("role")
        row["sector"] = cat_body.get("sector")
        card = client.get(f"/api/catalog/{a['role_id']}/card")
        sk = (card.json().get("sk") or []) if card.status_code == 200 else []
        row["n_skills"] = len(sk)
        row["top_skills"] = [s.get("skill") or s.get("title") or "" for s in sk[:5]]
        agents.append(row)
    return {
        "team_id": body.get("team_id"),
        "agents": agents,
        "artifacts_needed": body.get("artifacts_needed") or [],
    }


def run_oneshot(client, persona: dict, clock: _Clock) -> dict:
    r = client.post_limited("/api/team/recommend", json={"use_case": persona["task_utterance"]})
    if r.status_code != 200:
        raise RuntimeError(f"recommend HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    return {
        "transcript": [{"who": "user", "text": persona["task_utterance"]}],
        "brief": None,
        "intake_questions": None,
        "rounds": 0,
        "team": _enrich_team(client, body, clock),
        # Phase B fields (functional decompose-then-retrieve recall signal).
        # Default to [] so older/degraded responses don't blow up downstream.
        "functions_needed": body.get("functions_needed") or [],
        "functions_uncovered": body.get("functions_uncovered") or [],
    }


def run_intake(client, persona: dict, clock: _Clock) -> dict:
    r = client.post_limited("/api/intake/start")
    if r.status_code != 200:
        raise RuntimeError(f"intake start HTTP {r.status_code}: {r.text[:300]}")
    s = r.json()
    sid = s["session_id"]
    all_questions: list[str] = list(s.get("questions") or [])
    transcript: list[dict] = [{"who": "app", "text": q} for q in all_questions]

    # First round answers the seed questions with the raw task utterance.
    answers = [persona["task_utterance"]]
    brief = None
    rounds = 0
    while rounds < MAX_INTAKE_ROUNDS:
        clock.check()
        rounds += 1
        transcript += [{"who": "user", "text": a} for a in answers]
        r = client.post_limited(f"/api/intake/{sid}/answer", json={"answers": answers})
        if r.status_code != 200:
            raise RuntimeError(f"intake answer HTTP {r.status_code}: {r.text[:300]}")
        turn = r.json()
        if turn.get("ready"):
            brief = turn.get("brief")
            break
        qs = turn.get("questions") or []
        all_questions.extend(qs)
        transcript += [{"who": "app", "text": q} for q in qs]
        clock.check()
        answers = sim_user.answer_questions(persona, transcript, qs)

    if brief is None:
        raise RuntimeError(f"intake never reached ready in {rounds} rounds")

    clock.check()
    r = client.post_limited(f"/api/intake/{sid}/recommend")
    if r.status_code != 200:
        raise RuntimeError(f"intake recommend HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    return {
        "transcript": transcript,
        "brief": brief,
        "intake_questions": all_questions,
        "rounds": rounds,
        "team": _enrich_team(client, body, clock),
        # Phase B fields — /api/intake/{sid}/recommend shares team_service.recommend
        # with the one-shot path, so it carries the same functions_needed/
        # functions_uncovered pair. Default to [] for degraded responses.
        "functions_needed": body.get("functions_needed") or [],
        "functions_uncovered": body.get("functions_uncovered") or [],
    }


TRACKS = {"oneshot": run_oneshot, "intake": run_intake}


# ── one conversation end-to-end ─────────────────────────────────────────────
def run_conversation(client, persona: dict, track: str, run_id: str,
                     stop_flag: threading.Event) -> dict:
    clock = _Clock(PERSONA_WALL_SECONDS, stop_flag)
    record = {
        "run_id": run_id,
        "persona_id": persona["id"],
        "sector": persona["sector"],
        "persona": {k: persona[k] for k in
                    ("name", "occupation", "business_context", "task_utterance", "style")},
        "track": track,
        "status": "ok",
        "error": None,
        "timing": {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
    }
    try:
        record.update(TRACKS[track](client, persona, clock))
    except PersonaTimeout:
        record["status"] = "timeout"
        record["error"] = f"exceeded {PERSONA_WALL_SECONDS}s wall clock"
    except StopRun:
        raise
    except Exception as e:  # noqa: BLE001 — one bad conversation must not kill the run
        record["status"] = "error"
        record["error"] = f"{type(e).__name__}: {e}"[:400]
        record["traceback"] = traceback.format_exc()[-1500:]
    record["timing"]["seconds"] = clock.elapsed()
    record["timing"]["rounds"] = record.get("rounds", 0)

    if record["status"] == "ok":
        record["structural"] = judge_mod.structural_checks(record)
        verdict = judge_mod.judge_conversation(persona, record)
        if verdict is None:
            record["judge"] = None
            record["structural"]["errors"].append("judge_failed")
        else:
            record["judge"] = verdict
    return record


# ── the run loop ────────────────────────────────────────────────────────────
def _completed_keys(jsonl_path: Path) -> set[tuple[str, str]]:
    done = set()
    if jsonl_path.exists():
        for line in jsonl_path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("status") in ("ok", "timeout"):  # errors get retried
                done.add((rec["persona_id"], rec["track"]))
    return done


def run(client, personas: list[dict], tracks: list[str], out_dir: Path,
        resume: bool, concurrency: int = 1) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "conversations.jsonl"
    run_id = out_dir.name

    done = _completed_keys(jsonl_path) if resume else set()
    work = [(p, t) for p in personas for t in tracks if (p["id"], t) not in done]
    total = len(personas) * len(tracks)
    print(f"[run] {len(work)} conversations to do ({total - len(work)} already complete) "
          f"→ {jsonl_path}")

    stop_flag = threading.Event()

    def _sigint(_sig, _frm):
        if stop_flag.is_set():
            raise KeyboardInterrupt  # second Ctrl-C: bail hard
        print("\n[run] Ctrl-C — finishing in-flight conversations then stopping "
              "(press again to abort)")
        stop_flag.set()

    old_handler = signal.signal(signal.SIGINT, _sigint)
    write_lock = threading.Lock()
    n_done = 0

    def _one(item):
        if stop_flag.is_set():  # queued after Ctrl-C — don't even start
            raise StopRun()
        persona, track = item
        return run_conversation(client, persona, track, run_id, stop_flag)

    try:
        with open(jsonl_path, "a", encoding="utf-8") as fh:
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
                futures = {}
                for item in work:
                    if stop_flag.is_set():
                        break
                    futures[pool.submit(_one, item)] = item
                for fut in as_completed(futures):
                    persona, track = futures[fut]
                    try:
                        record = fut.result()
                    except StopRun:
                        continue
                    with write_lock:
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                        fh.flush()
                    n_done += 1
                    overall = (record.get("judge") or {}).get("overall")
                    print(f"[{n_done}/{len(work)}] {persona['id']}/{track}: "
                          f"{record['status']}"
                          + (f", structural={'PASS' if record.get('structural', {}).get('passed') else 'FAIL'}"
                             f", overall={overall}" if record["status"] == "ok" else
                             f" ({record.get('error')})"))
    finally:
        signal.signal(signal.SIGINT, old_handler)
    return jsonl_path
