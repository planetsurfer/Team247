"""Admin CLI to populate the graded battery for the top-N most-used roles —
Iteration 6 (user-value loop): battery top-20 + gallery receipts.

    python -m app.build_battery [--top N] [--only ROLE_ID ...] [--limit-skills K] [--force]

Role selection (see ``select_role_ids``): the top N roles by ``team_agents``
usage (``--top``, default 20) UNION the role_ids of the 5 starter-gallery
agents (app.build_gallery / gallery_agents) — always included so the
gallery's "proven" receipts (verify_service.verify, run separately on the
box) always have a battery to execute against. ``--only ROLE_ID ...``
(repeatable, or comma-separated) bypasses the top-N/gallery selection
entirely and builds exactly those role_ids — for smoke-testing a single
role locally.

Per role, up to ``--limit-skills`` (default 3, bounds LLM + sandbox cost)
executable skills are picked from ``role_skills`` (``is_executable = 1``,
highest ``required_level`` first). A role with ZERO executable skills is
reported and skipped — no fabricated battery item is ever inserted for it.

For each (role, skill) pair: skip if a 'ready' card_battery_items row
already exists (unless --force); otherwise reuse the EXISTING generation
machinery (battery.generate_skill) with a bounded RETRY LOOP (see
``_generate_and_validate`` — Iteration 6 follow-up, 2026-07-22) and persist
status='ready' only when validation passes (else 'invalid', kept for audit
rather than silently dropped). One role's failure (or one skill's) is
reported and never aborts the run.

Retry loop (up to MAX_ATTEMPTS=3 attempts per skill), added after a live
bounded check (5 real kimi-k2.6 generations, 0/5 ready) diagnosed TWO
recurring failure modes in generate_skill's own output:
  1. "bare_grade_format" — the grader's own last printed line is a bare
     number (`GRADE:0.5`) instead of the required `GRADE:{"score": 0.5}`
     JSON. This is DETERMINISTICALLY repairable (see
     ``_repair_bare_grade_grader``) without another LLM call: the grader's
     stdout is wrapped and, if the only defect is the bare-number format, a
     compliant line is reprinted after it (parse_grade scans bottom-up, so
     the repair wins) — tried FIRST since it's free and it was the more
     common failure mode observed live.
  2. "self_inconsistent" — properly formatted, but generate_skill's own
     reference_code doesn't actually satisfy its own grader_code (score
     < 0.99) — a genuine LLM self-consistency miss. This needs a fresh LLM
     attempt: generate_skill is called again with the concrete failure
     reason appended as a corrective turn (its `retry_hint` param — mirrors
     config.llm_json's own validate-and-retry-with-reason pattern, applied
     here at the whole generate+validate attempt level).
Only a validated attempt is ever persisted as 'ready'; after MAX_ATTEMPTS
failures the LAST attempt's item is kept as 'invalid' with the failure
reason logged (audit trail), never silently dropped.

Read by:
  app/services/verify_service.py  (verify() executes 'ready' rows)
  app/services/card_service.py    (battery_items() lists a role's items)
"""
from __future__ import annotations

import argparse
import json
import re
import textwrap
import time
from datetime import datetime, timezone

from app import db

import assess      # root: parse_grade — same bottom-up GRADE-line scanner verify() uses
import battery     # root: generate_skill — existing generation machinery
import config      # root: make_runner (LocalRunner, scrubbed env), SANDBOX_TIMEOUT
import framework   # root: get_context, get_ka (SFw xlsx, cached after first read)

DEFAULT_TOP_N = 20
DEFAULT_LIMIT_SKILLS = 3
MAX_ATTEMPTS = 3  # generate+validate attempts per skill before giving up as 'invalid'

# A bare `GRADE:<number>` line (no JSON) — the #1 failure mode diagnosed live
# 2026-07-22. Deliberately narrow: only matches a WHOLE line that is exactly
# "GRADE:" followed by a plain int/float, nothing else — never touches a line
# that's already the required JSON shape.
_BARE_GRADE_RE = re.compile(r"^GRADE:\s*(-?\d+(?:\.\d+)?)\s*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── role selection ──────────────────────────────────────────────────────────
def _top_role_ids(n: int) -> list[int]:
    rows = db.query(
        "SELECT ta.role_id AS role_id, COUNT(*) AS c FROM team_agents ta "
        "GROUP BY ta.role_id ORDER BY c DESC LIMIT ?",
        (n,),
    )
    return [r["role_id"] for r in rows]


def _gallery_role_ids() -> list[int]:
    """role_ids of the 5 starter-gallery agents (app.build_gallery), resolved via
    the team_agents row each gallery_agents.(team_id, agent_id) points at."""
    rows = db.query(
        "SELECT DISTINCT ta.role_id AS role_id FROM gallery_agents ga "
        "JOIN team_agents ta ON ta.team_id = ga.team_id AND ta.agent_id = ga.agent_id"
    )
    return [r["role_id"] for r in rows]


def select_role_ids(*, top: int = DEFAULT_TOP_N, only: set[int] | None = None) -> list[int]:
    """Role ids to populate this run: exactly ``only`` when given, else the
    top-N by team_agents usage UNION the gallery agents' role_ids (rank
    order preserved, gallery-only additions appended, deduped)."""
    if only is not None:
        return sorted(only)
    ordered = list(dict.fromkeys(_top_role_ids(top)))
    for rid in sorted(_gallery_role_ids()):
        if rid not in ordered:
            ordered.append(rid)
    return ordered


def _parse_only(raw: list[str] | None) -> set[int] | None:
    """Flatten repeated / comma-separated --only values into a role_id int set."""
    if not raw:
        return None
    out: set[int] = set()
    for v in raw:
        for s in v.split(","):
            s = s.strip()
            if s:
                out.add(int(s))
    return out or None


# ── per-role build ───────────────────────────────────────────────────────────
def _executable_skills(role_id: int, limit: int) -> list[dict]:
    return db.query(
        "SELECT code, skill, required_level FROM role_skills "
        "WHERE role_id = ? AND is_executable = 1 "
        "ORDER BY required_level DESC, code LIMIT ?",
        (role_id, limit),
    )


def _task_material(role_id: int) -> str | None:
    """Real market-demand flavour for generate_skill's optional `task_material`
    param, sourced from this role's already-distilled responsibilities (never
    fabricated) when present; None otherwise (generate_skill handles that)."""
    row = db.query(
        "SELECT responsibilities_distilled FROM cards WHERE role_id = ?",
        (role_id,), one=True,
    )
    if not row or not row.get("responsibilities_distilled"):
        return None
    try:
        bullets = json.loads(row["responsibilities_distilled"])
    except Exception:
        return None
    if isinstance(bullets, list) and bullets:
        return "; ".join(str(b) for b in bullets)
    return None


def _upsert_item(role_id: int, code: str, skill: str, level: int,
                  task_prompt: str, grader_code: str, reference_code: str | None,
                  status: str) -> None:
    """INSERT OR REPLACE on UNIQUE(role_id, code) — replaces a prior 'invalid'
    (retry) or 'ready' (--force rebuild) row for this skill; item_id is
    regenerated, which is fine — this script owns no history for the row."""
    db.execute(
        "INSERT OR REPLACE INTO card_battery_items(role_id, code, skill, required_level, "
        "task_prompt, grader_code, reference_code, source, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'generated', ?, ?)",
        (role_id, code, skill, level, task_prompt, grader_code, reference_code,
         status, _now()),
    )


# ── generate+validate retry loop (Iteration 6 follow-up, 2026-07-22) ────────
def _run_reference_grader(runner, item: dict) -> tuple[float, str, str | None]:
    """Run item['reference_code'] + item['grader_code'] in a fresh sandbox and
    return (score, raw_stdout, err) — the SAME exec shape battery._validate_item
    uses (compile-check both first; sandbox.process.code_run; assess.parse_grade),
    but exposing the raw stdout too, which battery._validate_item's bool-only
    return doesn't — needed here to DIAGNOSE *why* a failed attempt failed, not
    just that it failed. Never raises; a failure of any kind reports score=0.0."""
    try:
        compile(item["grader_code"], "<grader>", "exec")
        compile(item.get("reference_code") or "", "<ref>", "exec")
    except SyntaxError as e:
        return 0.0, "", f"syntax error: {e}"
    sb = runner.create()
    try:
        resp = sb.process.code_run(
            item["reference_code"] + "\n\n" + item["grader_code"],
            timeout=config.SANDBOX_TIMEOUT,
        )
        raw = getattr(resp, "result", "") or ""
        score, err = assess.parse_grade(raw)
        return score, raw, err
    except Exception as e:  # noqa: BLE001 — diagnostic helper must never raise
        return 0.0, "", str(e)
    finally:
        try:
            sb.delete()
        except Exception:
            pass


def _diagnose(raw_stdout: str) -> str:
    """Classify a failed attempt from its raw stdout's last 'GRADE:' line:
      'bare_grade_format' — deterministically repairable (see below)
      'self_inconsistent'  — proper JSON, reference just scores < 0.99
      'unknown'            — no GRADE line / garbled / anything else
    """
    for line in reversed((raw_stdout or "").strip().splitlines()):
        if not line.startswith("GRADE:"):
            continue
        if _BARE_GRADE_RE.match(line):
            return "bare_grade_format"
        rest = line[len("GRADE:"):].strip()
        try:
            obj = json.loads(rest)
        except Exception:
            return "unknown"
        if isinstance(obj, dict) and "score" in obj:
            return "self_inconsistent"
        return "unknown"
    return "unknown"


def _repair_bare_grade_grader(grader_code: str) -> str:
    """Deterministic repair for the 'bare_grade_format' defect: wrap the
    ENTIRE original grader_code's execution so its stdout is captured rather
    than printed directly; replay that captured stdout verbatim (so a grader
    that was ALREADY compliant is unaffected byte-for-byte); then, only if
    the last 'GRADE:' line in that captured output is a bare number, reprint
    one more compliant `GRADE:{"score": ...}` line after it — parse_grade
    (and battery._validate_item) scan bottom-up, so this repair line wins.

    Deliberately does NOT try to text-edit the original print statement (the
    score expression can be arbitrary Python) — wrapping the whole execution
    is robust to however the original code computed and printed its score.
    Always returns a syntactically-appendable string; if the repair harness
    itself doesn't fix anything (wrong defect, or grader_code has no GRADE
    line at all) the wrapped code just behaves identically to the original."""
    indented = textwrap.indent(grader_code, "    ")
    return (
        "import io as __rep_io, contextlib as __rep_ctx, json as __rep_json\n"
        "__rep_buf = __rep_io.StringIO()\n"
        "with __rep_ctx.redirect_stdout(__rep_buf):\n"
        f"{indented}\n"
        "__rep_out = __rep_buf.getvalue()\n"
        "print(__rep_out, end='')\n"
        "for __rep_line in reversed(__rep_out.strip().splitlines()):\n"
        "    if __rep_line.startswith('GRADE:'):\n"
        "        __rep_rest = __rep_line[len('GRADE:'):].strip()\n"
        "        try:\n"
        "            __rep_ok = isinstance(__rep_json.loads(__rep_rest), dict)\n"
        "        except Exception:\n"
        "            __rep_ok = False\n"
        "        if not __rep_ok:\n"
        "            try:\n"
        "                print('GRADE:' + __rep_json.dumps({'score': float(__rep_rest)}))\n"
        "            except Exception:\n"
        "                pass\n"
        "        break\n"
    )


def _generate_and_validate(role: str, skill: str, code: str, level: int, context: dict,
                            ka: dict, task_material: str | None, runner) -> dict:
    """Up to MAX_ATTEMPTS generate+validate attempts for one (role, skill).
    Order per attempt: LLM-generate (with the prior failure fed back as
    retry_hint after attempt 1) -> validate as-is -> if that failed with the
    deterministic 'bare_grade_format' defect, repair + re-validate WITHOUT
    burning another LLM call before deciding the attempt failed.

    Returns {status: 'ready'|'invalid', item: {...}, attempts: int,
    reason: str|None} — item always holds the LAST attempt's content (so an
    exhausted-invalid item still gets a real, audit-able task_prompt/grader/
    reference rather than nothing)."""
    hint = None
    item = None
    reason = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            gen = battery.generate_skill(role, skill, level, context, ka, task_material,
                                         retry_hint=hint, purpose="battery_gen")
        except Exception as e:  # noqa: BLE001 — feed the error back and keep trying
            reason = f"generation error: {e}"
            hint = reason
            continue

        item = {"skill": skill, "code": code, "required_level": level,
                "task_prompt": gen["task_prompt"], "grader_code": gen["grader_code"],
                "reference_code": gen["reference_code"]}

        score, raw, err = _run_reference_grader(runner, item)
        if score >= 0.99:
            return {"status": "ready", "item": item, "attempts": attempt, "reason": None}

        mode = _diagnose(raw)
        if mode == "bare_grade_format":
            repaired = {**item, "grader_code": _repair_bare_grade_grader(item["grader_code"])}
            score2, raw2, err2 = _run_reference_grader(runner, repaired)
            if score2 >= 0.99:
                return {"status": "ready", "item": repaired, "attempts": attempt, "reason": None}
            reason = (f"bare 'GRADE:<number>' format (auto-repaired), but reference_code "
                      f"still scores {score2:.2f} against its own grader (need >=0.99)")
        elif mode == "self_inconsistent":
            reason = (f"reference_code scores {score:.2f} against its OWN grader_code "
                      f"(need >=0.99) — generation self-consistency miss")
        else:
            reason = f"validation failed: {err or 'no parseable GRADE line in grader output'}"

        hint = reason

    return {"status": "invalid", "item": item, "attempts": MAX_ATTEMPTS, "reason": reason}


def _build_role(role_id: int, limit_skills: int, force: bool) -> dict:
    """Populate up to `limit_skills` executable-skill battery items for one
    role. Returns {role_id, role, generated, validated, invalid, skipped,
    seconds} plus one of {"no_executable": True} / {"error": "..."} when the
    role yields nothing to build."""
    t0 = time.perf_counter()
    role_row = db.query("SELECT * FROM roles WHERE role_id = ?", (role_id,), one=True)
    if role_row is None:
        return {"role_id": role_id, "role": None, "generated": 0, "validated": 0,
                "invalid": 0, "skipped": 0, "seconds": 0.0, "error": "unknown role_id"}
    role = role_row["role"]

    exec_skills = _executable_skills(role_id, limit_skills)
    if not exec_skills:
        return {"role_id": role_id, "role": role, "generated": 0, "validated": 0,
                "invalid": 0, "skipped": 0,
                "seconds": round(time.perf_counter() - t0, 1), "no_executable": True}

    context = framework.get_context(role)
    task_material = _task_material(role_id)
    runner = config.make_runner()

    generated = validated = invalid = skipped = 0
    for s in exec_skills:
        code, skill, level = s["code"], s["skill"], s["required_level"]
        existing = db.query(
            "SELECT status FROM card_battery_items WHERE role_id = ? AND code = ?",
            (role_id, code), one=True,
        )
        if existing and existing["status"] == "ready" and not force:
            skipped += 1
            continue

        ka = framework.get_ka(code, level)
        generated += 1
        result = _generate_and_validate(role, skill, code, level, context, ka,
                                        task_material, runner)
        status, item, attempts, reason = (result["status"], result["item"],
                                          result["attempts"], result["reason"])
        if status == "ready":
            validated += 1
        else:
            invalid += 1
        if item is None:
            # every attempt raised before producing any content (e.g. the LLM
            # call itself always errored) — nothing to persist, audit via log only
            print(f"  [{role_id} {role!r}] {skill} L{level}: invalid "
                  f"(no content generated in {attempts} attempt(s) — {reason})")
            continue
        _upsert_item(role_id, code, skill, level, item["task_prompt"],
                     item["grader_code"], item["reference_code"], status)
        suffix = f" — {reason}" if reason else ""
        print(f"  [{role_id} {role!r}] {skill} L{level}: {status} "
              f"({attempts}/{MAX_ATTEMPTS} attempt(s)){suffix}")

    return {"role_id": role_id, "role": role, "generated": generated,
            "validated": validated, "invalid": invalid, "skipped": skipped,
            "seconds": round(time.perf_counter() - t0, 1)}


def run(*, top: int = DEFAULT_TOP_N, only: set[int] | None = None,
        limit_skills: int = DEFAULT_LIMIT_SKILLS, force: bool = False) -> dict:
    """Build (or skip) battery items for every selected role. Never raises on
    a single role's failure — returns per-role reports + totals."""
    role_ids = select_role_ids(top=top, only=only)
    print(f"battery: {len(role_ids)} role(s) selected: {role_ids}")

    total_t0 = time.perf_counter()
    totals = {"roles": 0, "generated": 0, "validated": 0, "invalid": 0,
              "skipped": 0, "no_executable": 0, "failed_roles": 0}
    per_role = []
    for role_id in role_ids:
        try:
            r = _build_role(role_id, limit_skills, force)
        except Exception as e:  # noqa: BLE001 — one role's failure must never kill the run
            totals["failed_roles"] += 1
            print(f"[role {role_id}] FAILED: {e}")
            continue

        per_role.append(r)
        totals["roles"] += 1
        totals["generated"] += r["generated"]
        totals["validated"] += r["validated"]
        totals["invalid"] += r["invalid"]
        totals["skipped"] += r["skipped"]

        if r.get("no_executable"):
            totals["no_executable"] += 1
            print(f"[role {role_id} {r['role']!r}]: zero executable skills — skipped, no fabrication")
        elif r.get("error"):
            print(f"[role {role_id}]: {r['error']}")
        else:
            print(f"[role {role_id} {r['role']!r}]: generated={r['generated']} "
                  f"validated={r['validated']} invalid={r['invalid']} skipped={r['skipped']} "
                  f"({r['seconds']:.1f}s)")

    total_seconds = time.perf_counter() - total_t0
    totals["total_seconds"] = round(total_seconds, 1)
    totals["per_role"] = per_role
    print(f"\ndone: {totals['roles']} roles processed ({totals['failed_roles']} failed outright), "
          f"{totals['generated']} generated, {totals['validated']} validated, "
          f"{totals['invalid']} invalid, {totals['skipped']} skipped, "
          f"{totals['no_executable']} zero-executable — total {total_seconds:.1f}s")
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Populate card_battery_items for the top-N most-used roles "
                    "(Iteration 6 — battery top-20 + gallery receipts)."
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                         help=f"top N roles by team_agents usage (default {DEFAULT_TOP_N}); "
                              "the 5 starter-gallery agents' role_ids are always included too")
    parser.add_argument("--only", action="append", default=None, metavar="ROLE_ID",
                         help="only build these role_id(s) (repeatable, or comma-separated); "
                              "bypasses the --top/gallery-union selection")
    parser.add_argument("--limit-skills", type=int, default=DEFAULT_LIMIT_SKILLS,
                         dest="limit_skills",
                         help=f"cap executable skills generated per role (default "
                              f"{DEFAULT_LIMIT_SKILLS}) — bounds LLM + sandbox cost")
    parser.add_argument("--force", action="store_true",
                         help="regenerate even if a 'ready' battery item already exists")
    args = parser.parse_args()

    db.bootstrap()  # safe no-op if the schema already exists

    run(top=args.top, only=_parse_only(args.only),
        limit_skills=args.limit_skills, force=args.force)


if __name__ == "__main__":
    main()
