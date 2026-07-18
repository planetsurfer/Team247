"""Intake interview business logic (Phase 0).

Wraps the `intake_sessions` + `intake_messages` tables and the
`llm_contracts.intake` LLM call. The 3 fixed seed questions open every
interview; the LLM then asks tailored follow-ups round-by-round until it
declares the context sufficient (ready=true + Brief) or MAX_INTAKE_ROUNDS is
exceeded, in which case the loop is force-terminated with a minimal
best-effort brief so the SPA always reaches the recommend step.
"""
from __future__ import annotations

import datetime
import json
import uuid

from app import db, llm_contracts
from app.schemas import Brief  # noqa: F401 — re-exported shape used by callers


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _session_exists(session_id: str) -> bool:
    return db.query(
        "SELECT session_id FROM intake_sessions WHERE session_id = ?",
        (session_id,), one=True,
    ) is not None


def _require_session(session_id: str) -> None:
    if not _session_exists(session_id):
        raise ValueError("unknown session")


def _transcript(session_id: str) -> list[dict]:
    rows = db.query(
        "SELECT role, content, round FROM intake_messages "
        "WHERE session_id = ? ORDER BY msg_id",
        (session_id,),
    )
    return [
        {"role": r["role"], "content": r["content"], "round": r["round"]}
        for r in rows
    ]


def _persist_brief(session_id: str, brief_dict: dict) -> None:
    now = _now()
    db.execute(
        "UPDATE intake_sessions SET brief = ?, status = 'ready', updated_at = ? "
        "WHERE session_id = ?",
        (json.dumps(brief_dict), now, session_id),
    )


def start() -> dict:
    """Create a new intake session and emit the 3 fixed seed questions.

    Inserts an `intake_sessions` row (status='asking') and 3 `intake_messages`
    rows (role='fixed', round=0). Returns {"session_id", "questions"}.
    """
    session_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        "INSERT INTO intake_sessions (session_id, status, created_at, updated_at) "
        "VALUES (?, 'asking', ?, ?)",
        (session_id, now, now),
    )
    for q in llm_contracts.SEED_QUESTIONS:
        db.execute(
            "INSERT INTO intake_messages (session_id, role, content, round, created_at) "
            "VALUES (?, 'fixed', ?, 0, ?)",
            (session_id, q, now),
        )
    return {"session_id": session_id, "questions": list(llm_contracts.SEED_QUESTIONS)}


def answer(session_id: str, answers: list[str]) -> dict:
    """Accept the user's answers for the current round, then drive the LLM.

    R = (current max round over intake_messages) + 1. The user's answers are
    inserted at round R (role='user'), the full transcript is rebuilt, and
    `llm_contracts.intake` decides what happens next:

      * turn.ready  -> persist turn.brief, set status='ready',
                      return {"ready": true, "brief": <dict>}.
      * turn not ready and R >= MAX_INTAKE_ROUNDS -> force-terminate: persist a
        minimal {"outcome": <last user answer or "">, "pain_points": []} brief,
        set status='ready', return {"ready": true}.
      * otherwise -> insert the follow-ups as role='assistant' at round R and
        return {"ready": false, "questions": [<str>, ...]}.
    """
    _require_session(session_id)

    # Next round = max existing round + 1 (seed questions sit at round 0).
    row = db.query(
        "SELECT MAX(round) AS r FROM intake_messages WHERE session_id = ?",
        (session_id,), one=True,
    )
    R = (row["r"] if row and row["r"] is not None else -1) + 1

    now = _now()
    for a in answers:
        db.execute(
            "INSERT INTO intake_messages (session_id, role, content, round, created_at) "
            "VALUES (?, 'user', ?, ?, ?)",
            (session_id, a, R, now),
        )

    transcript = _transcript(session_id)
    turn = llm_contracts.intake(transcript)

    if turn.ready:
        brief_dict = turn.brief.model_dump(exclude_none=True)
        _persist_brief(session_id, brief_dict)
        return {"ready": True, "brief": brief_dict}

    if R >= llm_contracts.MAX_INTAKE_ROUNDS:
        # Cap reached -- guarantee termination with a minimal best-effort brief.
        user_msgs = [m for m in transcript if m["role"] == "user"]
        outcome = user_msgs[-1]["content"] if user_msgs else ""
        _persist_brief(session_id, {"outcome": outcome, "pain_points": []})
        return {"ready": True}

    # LLM wants another round -- record its follow-ups at the same round R.
    for q in turn.questions:
        db.execute(
            "INSERT INTO intake_messages (session_id, role, content, round, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, q, R, now),
        )
    return {"ready": False, "questions": list(turn.questions)}


def get(session_id: str) -> dict:
    """Return {"session_id", "status", "brief": <dict|None>, "transcript": [...]}.

    transcript entries are {"role", "content", "round"} ordered by msg_id.
    Raises ValueError("unknown session") if the session_id does not exist.
    """
    _require_session(session_id)
    row = db.query(
        "SELECT session_id, status, brief FROM intake_sessions WHERE session_id = ?",
        (session_id,), one=True,
    )
    brief_raw = row["brief"] if row else None
    brief = None
    if brief_raw:
        try:
            brief = json.loads(brief_raw)
        except Exception:
            brief = None
    return {
        "session_id": session_id,
        "status": row["status"] if row else "asking",
        "brief": brief,
        "transcript": _transcript(session_id),
    }
