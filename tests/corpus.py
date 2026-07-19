"""Corpus of realistic business-task inputs for the Team247 chat-flow test suite.

These are the kinds of plain-word requests a real user would type into the chat
frontend. Each is tagged with the artifact category the system should ideally
surface as a leading question ("do you have a sample / blank format / past
documents / a database to work from?") — the intake/refine capability the
product spec calls for. The 100%-guarantee test only requires that a team is
produced for every input; the artifact tags drive the coverage assertion.

Categories (the artifacts an honest agent-build should ask about — aligned to
the Artifact.kind enum in app/schemas.py):
  sample         — a worked example the user can hand over
  blank_format   — a blank template/format to fill
  past_documents — a corpus of past documents to mine
  database       — a live database/system of record to read or write
"""
from __future__ import annotations

from dataclasses import dataclass

# The artifact-bearing categories the coverage assertion requires the corpus
# to span. ("none" — pure reasoning, no artifact — is a valid label for an
# individual input but isn't something a rigorous suite should mandate, since
# a no-artifact task is rare and not meaningfully "covered" the same way.)
# These match the Artifact.kind enum in app/schemas.py exactly.
ARTIFACT_CATEGORIES = ("sample", "blank_format", "past_documents", "database")


@dataclass(frozen=True)
class TaskInput:
    """One realistic user task for the chat-flow suite."""
    use_case: str          # the free text a user types
    domain: str            # business domain (for balanced-coverage assertions)
    artifact: str          # expected artifact category — one of ARTIFACT_CATEGORIES
    notes: str = ""        # what a rigorous suite should additionally verify


# The corpus. Deliberately spans domains + artifact categories so the coverage
# assertion proves the suite isn't myopic. Inputs are plain, unstructured, and
# often underspecified — exactly the condition that should trigger leading
# questions rather than a one-shot guess.
CORPUS: tuple[TaskInput, ...] = (
    TaskInput(
        "I need to prepare quotations for my clients",
        domain="sales",
        artifact="sample",
        notes="should ask whether a past quote or a blank quote template exists",
    ),
    TaskInput(
        "I need to prepare invoices",
        domain="finance",
        artifact="database",
        notes="should ask whether invoices pull from a billing system / past invoice DB",
    ),
    TaskInput(
        "I need to check and vet through excel files for accurate pricing",
        domain="operations",
        artifact="sample",
        notes="should ask for a sample excel + the pricing rules to check against",
    ),
    TaskInput(
        "Reconcile our bank statements against the ledger every month",
        domain="finance",
        artifact="database",
        notes="should ask for ledger access + statement format",
    ),
    TaskInput(
        "Draft the weekly sales performance report for the management meeting",
        domain="sales",
        artifact="blank_format",
        notes="should ask for the report template / format management expects",
    ),
    TaskInput(
        "Onboard new vendors and capture their KYC documents",
        domain="procurement",
        artifact="blank_format",
        notes="should ask for the KYC checklist form / where to store it",
    ),
    TaskInput(
        "Clean and deduplicate our customer CRM export",
        domain="marketing",
        artifact="database",
        notes="should ask for CRM export + dedup rules",
    ),
    TaskInput(
        "Generate monthly payroll summaries from the timesheets",
        domain="hr",
        artifact="past_documents",
        notes="should ask for past summaries as a reference format",
    ),
    TaskInput(
        "Respond to RFPs by pulling boilerplate from our past proposals",
        domain="sales",
        artifact="past_documents",
        notes="should ask for the proposal library / past RFP responses",
    ),
    TaskInput(
        "Audit employee expense claims against company policy",
        domain="hr",
        artifact="sample",
        notes="should ask for the policy doc + a sample claim",
    ),
    TaskInput(
        "Forecast next quarter's inventory from historical sales",
        domain="operations",
        artifact="database",
        notes="should ask for the sales/history DB + forecast horizon",
    ),
    TaskInput(
        "Translate product spec sheets into marketing copy",
        domain="marketing",
        artifact="sample",
        notes="should ask for a spec sheet + a past good piece of copy",
    ),
    TaskInput(
        "Schedule and remind patients of their appointments",
        domain="healthcare",
        artifact="database",
        notes="should ask for the appointments system / patient DB",
    ),
    TaskInput(
        "Review supplier contracts for renewal and price-escalation risks",
        domain="procurement",
        artifact="past_documents",
        notes="should ask for the contract repository",
    ),
    TaskInput(
        "Build a dashboard tracking shipment statuses across our carriers",
        domain="operations",
        artifact="database",
        notes="should ask for carrier APIs / shipment DB",
    ),
    TaskInput(
        "Summarise customer support tickets into a weekly trends brief",
        domain="support",
        artifact="database",
        notes="should ask for the ticketing system export",
    ),
)

# Subset used by the (LLM-heavy, slower) intake-refinement test to bound cost.
INTAKE_SUBSET_INDICES = (0, 3, 8)


def corpus_ids() -> list[str]:
    """Stable pytest IDs: domain-index-of-input."""
    return [f"{t.domain}-{i}" for i, t in enumerate(CORPUS)]


def artifact_coverage() -> dict[str, int]:
    """Count of corpus inputs per artifact category — for the coverage assertion."""
    cov = {c: 0 for c in ARTIFACT_CATEGORIES}
    for t in CORPUS:
        cov[t.artifact] = cov.get(t.artifact, 0) + 1
    return cov
