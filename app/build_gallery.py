"""Admin CLI to seed the starter gallery — Iteration 4 (user-value loop).

    python -m app.build_gallery [--force] [--only SLUG ...]

For each of 5 fixed archetype use cases, this drives the SAME pipeline a real
user's first task does: team_service.recommend -> handoff_service.wire
(best-effort — a wiring failure is reported and the build continues unwired)
-> skill_bundle_service.compose_bundle for the primary (first-listed) agent
-> one row persisted to `gallery_agents` (migration 0007). Idempotent by
slug: an existing row is left untouched unless --force. One archetype
failing (LLM hiccup, guardrail miss, ...) is reported and skipped — it must
never abort the remaining archetypes.

--only bounds cost when iterating or smoke-testing locally: pass a slug
(repeatably, or comma-separated) to build/rebuild just that subset instead of
all 5.

Read by:
  GET /api/gallery         (open, metadata only)       app/routers/gallery.py
  GET /api/gallery/{slug}  (beta-gated, full row incl. bundle_md)
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from app import db
from app.services import handoff_service, skill_bundle_service, team_service

ARCHETYPES = [
    {
        "slug": "collections-chaser",
        "label": "Collections Chaser",
        "blurb": "Chases overdue invoices with the right urgency per account",
        "use_case": "chase up customers who owe us money",
    },
    {
        "slug": "contract-reviewer",
        "label": "Contract Reviewer",
        "blurb": "Clause-by-clause risk review of vendor contracts before you sign",
        "use_case": "review a vendor contract before signing",
    },
    {
        "slug": "quotation-writer",
        "label": "Quotation Writer",
        "blurb": "Formal quotations from an RFQ and your price list",
        "use_case": "prepare a quotation for a corporate client",
    },
    {
        "slug": "onboarding-coordinator",
        "label": "Onboarding Coordinator",
        "blurb": "Structured onboarding plans for new hires",
        "use_case": "onboard a new hire",
    },
    {
        "slug": "campaign-planner",
        "label": "Campaign Planner",
        "blurb": "Launch campaign plans that respect your brand rules",
        "use_case": "run a social media campaign for a product launch",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exists(slug: str) -> bool:
    return db.query("SELECT 1 FROM gallery_agents WHERE slug = ?", (slug,), one=True) is not None


def _build_one(archetype: dict, *, force: bool) -> tuple[bool, float]:
    """Build (or skip) one archetype. Returns (built, elapsed_seconds).

    built=False, elapsed=0.0 means the slug already existed and force wasn't
    set — a clean skip, not a failure.
    """
    slug = archetype["slug"]
    use_case = archetype["use_case"]

    if not force and _exists(slug):
        return False, 0.0

    t0 = time.perf_counter()

    rec = team_service.recommend(use_case=use_case)
    team_id = rec["team_id"]
    agents = rec.get("agents") or []
    if not agents:
        raise RuntimeError(f"recommend returned zero agents for {slug!r}")
    primary_agent_id = agents[0]["agent_id"]

    try:
        handoff_service.wire(team_id, use_case)
    except Exception as e:  # noqa: BLE001 — best-effort; bundle still composes fine unwired
        print(f"  [{slug}] wire failed (continuing unwired): {e}")

    bundle_md = skill_bundle_service.compose_bundle(team_id, primary_agent_id, use_case)

    if force and _exists(slug):
        db.execute("DELETE FROM gallery_agents WHERE slug = ?", (slug,))

    db.execute(
        "INSERT INTO gallery_agents(slug, label, blurb, use_case, team_id, agent_id, "
        "bundle_md, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (slug, archetype["label"], archetype["blurb"], use_case,
         team_id, primary_agent_id, bundle_md, _now()),
    )
    return True, time.perf_counter() - t0


def _parse_only(raw: list[str] | None) -> set[str] | None:
    """Flatten repeated / comma-separated --only values into a slug set."""
    if not raw:
        return None
    out: set[str] = set()
    for v in raw:
        out.update(s.strip() for s in v.split(",") if s.strip())
    return out or None


def run(*, force: bool = False, only: set[str] | None = None) -> dict:
    """Build (or skip) every archetype, or just `only` when given. Never
    raises on a single archetype's failure — returns
    {built, skipped, failed, total_seconds}."""
    archetypes = ARCHETYPES
    if only is not None:
        archetypes = [a for a in ARCHETYPES if a["slug"] in only]
        unknown = only - {a["slug"] for a in ARCHETYPES}
        if unknown:
            print(f"warning: unknown --only slug(s) ignored: {', '.join(sorted(unknown))}")

    total_t0 = time.perf_counter()
    built = skipped = failed = 0
    for a in archetypes:
        slug = a["slug"]
        try:
            did_build, elapsed = _build_one(a, force=force)
        except Exception as e:  # noqa: BLE001 — one archetype failing must never kill the rest
            failed += 1
            print(f"[{slug}] FAILED: {e}")
            continue
        if did_build:
            built += 1
            print(f"[{slug}] built in {elapsed:.1f}s")
        else:
            skipped += 1
            print(f"[{slug}] skipped (already exists — use --force to rebuild)")

    total_seconds = time.perf_counter() - total_t0
    print(f"\ndone: {built} built, {skipped} skipped, {failed} failed — total {total_seconds:.1f}s")
    return {"built": built, "skipped": skipped, "failed": failed, "total_seconds": total_seconds}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/rebuild the Team247 starter gallery.")
    parser.add_argument("--force", action="store_true",
                         help="rebuild even if a slug already exists")
    parser.add_argument("--only", action="append", default=None, metavar="SLUG",
                         help="only build this slug (repeatable, or comma-separated); "
                              "default: all 5 archetypes")
    args = parser.parse_args()

    db.bootstrap()  # safe no-op if the schema (incl. gallery_agents) already exists

    run(force=args.force, only=_parse_only(args.only))


if __name__ == "__main__":
    main()
