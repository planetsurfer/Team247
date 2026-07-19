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
from app.services import card_service

# The one deterministic industry-confirming exchange. Injected at most once per
# interview; the answer is matched against the OFFICIAL catalog sector names so
# brief.sector carries a verbatim official name that team_service can use
# directly (exact match → no inference guesswork on ambiguous task text).
SECTOR_QUESTION_MARKER = "official SkillsFuture sector"


def _official_sectors() -> list[str]:
    """Official catalog sector names (39), longest-first for greedy matching."""
    try:
        return sorted((row["sector"] for row in card_service.sectors()),
                      key=len, reverse=True)
    except Exception:  # noqa: BLE001 — sector capture must never break intake
        return []


def _match_official_sector(messages: list[str], names: list[str]):
    """Scan user messages (latest first) for an official sector name.

    Case-insensitive substring match, longest name first so 'Public Transport'
    wins over a hypothetical shorter overlap. Returns the verbatim official
    name or None.
    """
    for text in reversed(messages):
        low = (text or "").lower()
        for name in names:
            if name.lower() in low:
                return name
    return None


def _sector_question(user_text: str, names: list[str]) -> str:
    guesses = []
    try:
        guesses = llm_contracts.infer_sectors(user_text, names)
    except Exception:  # noqa: BLE001
        pass
    hint = (f" Our best guesses for you: {', or '.join(guesses)}."
            if guesses else " For example: Accountancy, Logistics, Food Services, Retail.")
    return (
        "So we match you with officially recognised roles: which industry are "
        "you in? Please reply with the specific official SkillsFuture sector "
        f"name.{hint}"
    )


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

    # The one industry-confirming exchange: match user answers against the
    # official sector names; the confirmed name overrides whatever free text
    # the LLM puts in brief.sector.
    sector_names = _official_sectors()
    user_msgs = [m["content"] for m in transcript if m["role"] == "user"]
    confirmed_sector = _match_official_sector(user_msgs, sector_names)
    sector_asked = any(
        SECTOR_QUESTION_MARKER in (m["content"] or "")
        for m in transcript if m["role"] in ("assistant", "fixed")
    )

    turn = llm_contracts.intake(transcript)

    if turn.ready:
        # "Ensure the system asks": a ready verdict may not skip the official-
        # sector exchange — hold readiness one round to ask it (once). The
        # MAX_INTAKE_ROUNDS force-terminate still guarantees termination.
        if sector_names and not confirmed_sector and not sector_asked \
                and R < llm_contracts.MAX_INTAKE_ROUNDS:
            q = _sector_question(" ".join(user_msgs), sector_names)
            db.execute(
                "INSERT INTO intake_messages (session_id, role, content, round, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, q, R, now),
            )
            return {"ready": False, "questions": [q]}
        brief_dict = turn.brief.model_dump(exclude_none=True)
        if confirmed_sector:
            brief_dict["sector"] = confirmed_sector
        _persist_brief(session_id, brief_dict)
        return {"ready": True, "brief": brief_dict}

    if R >= llm_contracts.MAX_INTAKE_ROUNDS:
        # Cap reached -- guarantee termination with a minimal best-effort brief.
        outcome = user_msgs[-1] if user_msgs else ""
        brief_dict = {"outcome": outcome, "pain_points": [], "artifacts_needed": []}
        if confirmed_sector:
            brief_dict["sector"] = confirmed_sector
        _persist_brief(session_id, brief_dict)
        return {"ready": True}

    # LLM wants another round -- record its follow-ups at the same round R.
    # If the official-sector exchange hasn't happened yet, inject its question
    # (once per interview), trimming the LLM's follow-ups to keep <=3 total.
    questions = list(turn.questions)
    if sector_names and not confirmed_sector and not sector_asked:
        questions = questions[:2] + [_sector_question(" ".join(user_msgs), sector_names)]
    for q in questions:
        db.execute(
            "INSERT INTO intake_messages (session_id, role, content, round, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, q, R, now),
        )
    return {"ready": False, "questions": questions}


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
