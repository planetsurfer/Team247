"""Admin CLI to seed the starter gallery — Iteration 4 (user-value loop).

    python -m app.build_gallery [--force] [--only SLUG ...]

For each fixed archetype use case, this drives the SAME pipeline a real
user's first task does: team_service.recommend -> handoff_service.wire
(best-effort — a wiring failure is reported and the build continues unwired)
-> (optional) team_service.set_user_inputs, when the archetype carries
`seed_inputs` -> skill_bundle_service.compose_bundle for the primary
(first-listed) agent -> one row persisted to `gallery_agents` (migration
0007). Idempotent by slug: an existing row is left untouched unless --force.
One archetype failing (LLM hiccup, guardrail miss, ...) is reported and
skipped — it must never abort the remaining archetypes.

An archetype may optionally carry `seed_inputs`: a list of
{kind, name, content} dicts in the same shape PUT /api/team/{team_id}/inputs
accepts (see team_service.USER_INPUT_KINDS). When present, they're written
via team_service.set_user_inputs AFTER wire and BEFORE compose_bundle, so
the stored gallery bundle_md carries a "### Your provided inputs" section
seeded with realistic ops guidance instead of shipping empty. Archetypes
without `seed_inputs` behave exactly as before (no call, no section).

--only bounds cost when iterating or smoke-testing locally: pass a slug
(repeatably, or comma-separated) to build/rebuild just that subset instead of
all archetypes.

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

# ──────────────────────────────────────────────────────────────────────────────
# Trades & renovation business archetypes — shared seed_inputs (see module
# docstring) plus one archetype-specific input each. Framed generically for
# ANY trades/renovation business: no company names, no person names, no
# skill/qualification codes.
# ──────────────────────────────────────────────────────────────────────────────
_TRADES_SHARED_SEED_INPUTS = [
    {
        "kind": "constraint",
        "name": "Tools we already use",
        "content": (
            "Work within the business's existing tools only (accounting, "
            "payroll, job management, CRM) — never propose adopting new "
            "software platforms."
        ),
    },
    {
        "kind": "procedure",
        "name": "Human review rule",
        "content": (
            "Anything money-related or customer-facing is prepared as a "
            "DRAFT for a person to review and send — never sent "
            "automatically."
        ),
    },
    {
        "kind": "constraint",
        "name": "What stays human",
        "content": (
            "Closing, negotiation, pricing decisions, site assessment and "
            "technical judgement stay with people; provide analysis, never "
            "the final call."
        ),
    },
]

ARCHETYPES += [
    {
        "slug": "variation-order-capturer",
        "label": "Variation-Order Capturer",
        "blurb": (
            "Captures on-site scope additions so they reach the invoice — "
            "the biggest leak in renovation work"
        ),
        "use_case": "capture on-site variation orders and scope additions so they get invoiced",
        "seed_inputs": _TRADES_SHARED_SEED_INPUTS + [
            {
                "kind": "workaround",
                "name": "Why this matters",
                "content": (
                    "On-site scope additions that never reach an invoice are "
                    "typically the largest single revenue leak in renovation "
                    "work; record each addition with dimensions, specs and "
                    "agreed price the moment it is agreed."
                ),
            },
        ],
    },
    {
        "slug": "quote-followup-chaser",
        "label": "Quote Follow-Up Chaser",
        "blurb": (
            "Makes a forgotten quote structurally unlikely — chases every "
            "no-response quote with drafted follow-ups"
        ),
        "use_case": "follow up on quotes that received no response",
        "seed_inputs": _TRADES_SHARED_SEED_INPUTS + [
            {
                "kind": "procedure",
                "name": "Follow-up cadence",
                "content": (
                    "Acknowledge new enquiries immediately; chase quotes "
                    "with no response on a steady cadence; every chase "
                    "message is a draft for a person to send."
                ),
            },
        ],
    },
    {
        "slug": "maintenance-agreement-converter",
        "label": "Maintenance Agreement Converter",
        "blurb": "Turns one-off jobs into recurring maintenance revenue",
        "use_case": "convert one-off customers into recurring maintenance agreements",
        "seed_inputs": _TRADES_SHARED_SEED_INPUTS + [
            {
                "kind": "procedure",
                "name": "Conversion moments",
                "content": (
                    "Use the warranty period as a scheduled contact point; "
                    "reactivate past customers systematically; ask for "
                    "referrals; business customers are repeat buyers by "
                    "nature."
                ),
            },
        ],
    },
    {
        "slug": "margin-by-job-reporter",
        "label": "Margin-by-Job Reporter",
        "blurb": (
            "Shows whether project work genuinely out-earns handyman work "
            "— margin by job type, discounting made visible"
        ),
        "use_case": "report margin by job type and make discounting visible",
        "seed_inputs": _TRADES_SHARED_SEED_INPUTS + [
            {
                "kind": "metric",
                "name": "What to measure",
                "content": (
                    "Margin by job type (project vs handyman), costing "
                    "accuracy against quotes to reduce under-quoting, and "
                    "visibility of all discounting."
                ),
            },
        ],
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

    seed_inputs = archetype.get("seed_inputs")
    if seed_inputs:
        team_service.set_user_inputs(team_id, seed_inputs)

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
                              "default: all archetypes")
    args = parser.parse_args()

    db.bootstrap()  # safe no-op if the schema (incl. gallery_agents) already exists

    run(force=args.force, only=_parse_only(args.only))


if __name__ == "__main__":
    main()
