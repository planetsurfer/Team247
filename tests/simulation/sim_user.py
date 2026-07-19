"""In-character simulated user for the intake interview (purpose "sim_user").

Given the persona card, the Q&A so far, and the app's new follow-up questions,
returns one plain-language answer per question. Tolerates answer-count
mismatch (pad/truncate) and falls back to the corpus suite's generic template
answer if the LLM fails all retries, so a run never stalls on the simulator.
"""
from __future__ import annotations

from app.llm_contracts import LLMError, call_llm_json

SYSTEM = """You are role-playing an ordinary working person talking to a business
chat assistant. Stay strictly in character.

Answer the assistant's questions the way THIS person would:
- Plain everyday language, 1-3 short sentences per question. No jargon.
- Ground answers in the persona's facts. If a question asks about something
  not covered by the facts, improvise a mundane, consistent detail — never
  say "as an AI" or break character.
- Match the persona's style (terse personas answer in fragments; vague
  personas are a bit unsure; rambling personas add asides).
- If asked whether you have documents/samples/systems, answer concretely
  from the facts (e.g. "yeah we have last year's ones in a folder").
- If a question asks for your OFFICIAL industry/sector name (e.g. it mentions
  "official SkillsFuture sector"), answer with the exact official sector name
  from your persona card — a real person knows their industry and would name
  it. Everyday phrasing for everything else.
- Answer strictly by question number: answers[0] answers question 1,
  answers[1] answers question 2, and so on — never merge or reorder.

Return STRICT JSON only: {"answers": ["...", ...]} — exactly one answer per
question, in order."""


def _mk_validate(n_questions: int):
    def _validate(obj):
        ans = obj.get("answers")
        if not isinstance(ans, list) or not ans:
            raise ValueError("answers must be a non-empty list")
        ans = [str(a).strip() for a in ans if str(a).strip()]
        if not ans:
            raise ValueError("answers all empty")
        # Tolerate count mismatch: pad with the last answer / truncate.
        while len(ans) < n_questions:
            ans.append(ans[-1])
        obj["answers"] = ans[:max(1, n_questions)]
        return obj
    return _validate


def _fallback(persona: dict, questions: list[str]) -> list[str]:
    text = (f"For this task ({persona['task_utterance']}): I'd provide whatever "
            f"you need — please tell me which.")
    return [text] * max(1, len(questions))


def answer_questions(persona: dict, transcript: list[dict], questions: list[str]) -> list[str]:
    """One in-character answer per question. transcript = [{who, text}, ...]."""
    card = (
        f"Name: {persona['name']}\n"
        f"Occupation: {persona['occupation']}\n"
        f"Official industry sector (use this exact name if asked for your "
        f"official sector): {persona['sector']}\n"
        f"Business: {persona['business_context']}\n"
        f"Style: {persona['style']}\n"
        f"Task they came for: {persona['task_utterance']}\n"
        f"Facts:\n" + "\n".join(f"- {f}" for f in persona["facts"])
    )
    history = "\n".join(
        f"{'ASSISTANT' if t['who'] == 'app' else 'YOU'}: {t['text']}" for t in transcript
    ) or "(conversation just started)"
    qs = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
    user = (
        f"PERSONA CARD:\n{card}\n\nCONVERSATION SO FAR:\n{history}\n\n"
        f"THE ASSISTANT NOW ASKS:\n{qs}\n\nAnswer each question in character."
    )
    try:
        obj = call_llm_json(
            "sim_user",
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            _mk_validate(len(questions)),
            temperature=0.7,
        )
        return obj["answers"]
    except LLMError:
        return _fallback(persona, questions)
