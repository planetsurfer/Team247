"""Persona generation + caching for the simulation harness.

One LLM call per catalog sector (purpose "sim_persona") generates PER_SECTOR
personas — everyday workers with a plain-language task they'd type into the
chat. Cached to personas.json next to this module so reruns are reproducible;
--regen-personas rebuilds. Subsampling to --personas N is seeded so the same
N always picks the same personas.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from app.llm_contracts import call_llm_json

PER_SECTOR = 3
CACHE_PATH = Path(__file__).parent / "personas.json"
STYLES = ("terse", "chatty", "vague", "rambling")

SYSTEM = """You invent realistic PERSONAS of everyday working people for testing a
business chat assistant. The personas are ordinary staff (owners, admins,
coordinators, technicians — NOT tech people) who describe a routine work task
in their own plain words.

Rules for task_utterance:
- Plain, non-technical, everyday language — exactly what a busy person types
  into a chat box. 1-2 sentences.
- Vary the register per persona's style: terse ("need help w invoices"),
  chatty, vague, or rambling. Occasional lowercase/typos are GOOD for
  terse/vague styles. Never use words like "workflow", "automation",
  "agent", "AI", "system integration".
- The task must be a real recurring job in that sector (rostering, chasing
  payments, stock counts, patient reminders, tender paperwork, ...).

Return STRICT JSON only:
{"personas": [{"name": str, "occupation": str, "business_context": str,
  "task_utterance": str, "style": "terse|chatty|vague|rambling",
  "facts": [str, ...]}]}

- business_context: one line (company size, what the business does).
- facts: 3-5 concrete ground truths the persona can draw on when answering
  follow-up questions (e.g. "uses a shared Excel tracker", "about 40 invoices
  a month", "boss wants it in the same format as last year"). Plain facts,
  no solutions."""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _validate_personas(obj):
    ps = obj.get("personas")
    if not isinstance(ps, list) or not ps:
        raise ValueError("personas must be a non-empty list")
    for p in ps:
        for k in ("name", "occupation", "business_context", "task_utterance", "facts"):
            if not p.get(k):
                raise ValueError(f"persona missing {k}: {p}")
        if p.get("style") not in STYLES:
            p["style"] = "chatty"
        if not isinstance(p["facts"], list) or len(p["facts"]) < 2:
            raise ValueError(f"persona facts too thin: {p}")
    return obj


def load_sectors(client) -> list[str]:
    """All catalog sectors, ordered by role count desc (server's order)."""
    r = client.get("/api/catalog/sectors")
    r.raise_for_status()
    return [row["sector"] for row in r.json()]


def generate_personas(sectors: list[str], per_sector: int = PER_SECTOR) -> list[dict]:
    out = []
    for sector in sectors:
        user = (
            f"Sector: {sector}\n"
            f"Invent {per_sector} distinct personas from DIFFERENT kinds of "
            f"businesses in this sector, each with a different style and a "
            f"different routine task."
        )
        obj = call_llm_json(
            "sim_persona",
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            _validate_personas,
            temperature=0.8,
        )
        for i, p in enumerate(obj["personas"][:per_sector], start=1):
            p["id"] = f"{_slug(sector)}-{i}"
            p["sector"] = sector
            out.append(p)
        print(f"[personas] {sector}: {min(per_sector, len(obj['personas']))} generated")
    return out


def get_personas(client, n: int, sectors_filter: list[str] | None = None,
                 regen: bool = False, seed: int = 42) -> list[dict]:
    """Load cached personas (generating if absent/regen), filter, subsample to n."""
    if CACHE_PATH.exists() and not regen:
        personas = json.loads(CACHE_PATH.read_text())
    else:
        sectors = load_sectors(client)
        if sectors_filter:
            sectors = [s for s in sectors if s in sectors_filter]
        personas = generate_personas(sectors)
        if sectors_filter:
            # Partial generation (e.g. a smoke run) must not poison the cache
            # that a later full run would silently reuse.
            print(f"[personas] {len(personas)} generated for filtered sectors — NOT cached")
        else:
            CACHE_PATH.write_text(json.dumps(personas, indent=2, ensure_ascii=False))
            print(f"[personas] cached {len(personas)} → {CACHE_PATH}")
    if sectors_filter:
        personas = [p for p in personas if p["sector"] in sectors_filter]
    if n and n < len(personas):
        # Seeded, sector-balanced subsample: round-robin across sectors so a
        # small n still spans many sectors instead of clustering.
        rng = random.Random(seed)
        by_sector: dict[str, list] = {}
        for p in personas:
            by_sector.setdefault(p["sector"], []).append(p)
        for group in by_sector.values():
            rng.shuffle(group)
        order = sorted(by_sector)
        rng.shuffle(order)
        picked, i = [], 0
        while len(picked) < n:
            sector = order[i % len(order)]
            if by_sector[sector]:
                picked.append(by_sector[sector].pop())
            elif all(not g for g in by_sector.values()):
                break
            i += 1
        personas = picked
    return personas
