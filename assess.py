import json, math, time
from config import SANDBOX_TIMEOUT
from candidate import candidate_solve
from events import emit

def parse_grade(stdout):
    """Bottom-up scan for the GRADE line; clamp; garbled/missing -> 0 + note (§0.5 bug 6)."""
    for line in reversed((stdout or "").strip().splitlines()):
        if line.startswith("GRADE:"):
            try:
                s = float(json.loads(line[len("GRADE:"):])["score"])
                if not math.isfinite(s):
                    raise ValueError("score must be finite")
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

def assess_skill(runner, item, agent_spec):
    """Fresh sandbox -> candidate code + grader together -> parse GRADE -> teardown in finally."""
    skill, req = item["skill"], item["required_level"]
    sandbox, sid, t0, err = None, "?", time.time(), None
    score = 0.0
    try:
        sandbox = runner.create()
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
                    runner.delete(sandbox)
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
