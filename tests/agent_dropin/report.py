"""Aggregate results.jsonl into a markdown report (also --report-only)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from tests.agent_dropin.judge import COMP_DIMS, WORK_DIMS

DELIVERABLE_PREVIEW_LINES = 30


def _load(jsonl_path: Path) -> list[dict]:
    records = []
    for line in jsonl_path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # Keep only the latest record per archetype — resume may retry errors.
    latest: dict[str, dict] = {}
    for r in records:
        latest[r.get("archetype")] = r
    return list(latest.values())


def _fmt(x, nd=2):
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "—"


def _dim_means(records: list[dict], judge_key: str, dims: tuple[str, ...]) -> dict:
    out = {}
    verdicts = [r[judge_key] for r in records if r.get(judge_key)]
    for dim in dims:
        vals = [v["scores"].get(dim) for v in verdicts if isinstance(v["scores"].get(dim), int)]
        out[dim] = mean(vals) if vals else None
    vals = [v["overall"] for v in verdicts]
    out["overall"] = mean(vals) if vals else None
    return out


def _norm_gap(text: str) -> str:
    return " ".join(text.lower().strip().rstrip(".").split())


def write_report(jsonl_path: Path, md_path: Path, config_echo: str = "") -> Path:
    records = _load(jsonl_path)
    ok = [r for r in records if r.get("status") == "ok"]
    errored = [r for r in records if r.get("status") == "error"]

    lines = [
        "# Team247 agent drop-in eval report",
        "",
        f"Source: `{jsonl_path}` — {len(records)} archetypes "
        f"({len(ok)} ok, {len(errored)} error).",
        "",
        "> **What this measures:** for each archetype, (1) does the "
        "generated SKILL.md read as a usable, honest, paste-in agent "
        "definition (comprehensiveness judge, no execution needed), and "
        "(2) when actually run headless via the `claude` CLI on a realistic "
        "scenario, does the resulting work product hold up (work judge, "
        "cross-model — kimi judging Claude's output).",
        "",
    ]
    if config_echo:
        lines += [f"Run config: {config_echo}", ""]

    # ── summary table ──────────────────────────────────────────────────────
    lines += [
        "## Summary", "",
        "| Archetype | Selected role | Team size | Well-formed | Comp overall | "
        "Work overall | Harness status |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        team = r.get("team") or {}
        det = r.get("deterministic") or {}
        comp = r.get("comp_judge") or {}
        work = r.get("work_judge") or {}
        harness = r.get("harness") or {}
        lines.append(
            f"| {r.get('archetype')} | {team.get('selected_role', '—')} | "
            f"{team.get('n_agents', '—')} | "
            f"{'yes' if det.get('checks', {}).get('well_formed') else 'no' if det else '—'} | "
            f"{_fmt(comp.get('overall'), 0) if comp.get('overall') is not None else '—'} | "
            f"{_fmt(work.get('overall'), 0) if work.get('overall') is not None else '—'} | "
            f"{harness.get('status', r.get('status'))} |"
        )
    lines.append("")

    # ── per-dim means ──────────────────────────────────────────────────────
    comp_means = _dim_means(ok, "comp_judge", COMP_DIMS)
    work_means = _dim_means(ok, "work_judge", WORK_DIMS)
    lines += ["## Comprehensiveness judge — mean scores", ""]
    for dim in (*COMP_DIMS, "overall"):
        lines.append(f"- {dim}: **{_fmt(comp_means[dim])}**")
    lines += ["", "## Work-product judge — mean scores", ""]
    for dim in (*WORK_DIMS, "overall"):
        lines.append(f"- {dim}: **{_fmt(work_means[dim])}**")
    lines.append("")

    # ── deterministic-failure taxonomy ────────────────────────────────────
    det_fails = Counter(
        e for r in ok for e in (r.get("deterministic") or {}).get("errors", [])
    )
    lines += ["## Deterministic-check failure taxonomy", ""]
    lines += [f"- `{t}`: {n}" for t, n in det_fails.most_common()] or ["- (none)"]
    lines.append("")

    # ── per-archetype detail ──────────────────────────────────────────────
    lines += ["## Per-archetype detail", ""]
    for r in records:
        team = r.get("team") or {}
        comp = r.get("comp_judge") or {}
        work = r.get("work_judge") or {}
        harness = r.get("harness") or {}
        lines += [
            f"### {r.get('archetype')} — {r.get('label', '')}", "",
            f"- Status: **{r.get('status')}**" + (f" — {r.get('error')}" if r.get('error') else ""),
            f"- Roles on team: {', '.join(team.get('roles') or []) or '(n/a)'}",
            f"- Selected agent: `{team.get('selected_agent_id', '—')}` "
            f"({team.get('selected_role', '—')}), selection mode: "
            f"**{team.get('selection', '—')}**",
        ]
        if comp:
            lines.append(f"- Comprehensiveness: overall {comp.get('overall')}/5 — {comp.get('rationale')}")
        if harness:
            lines.append(f"- Harness: {harness.get('status')} "
                         f"({harness.get('seconds', '—')}s, "
                         f"{harness.get('deliverable_chars', 0)} chars)")
        if work:
            lines.append(f"- Work product: overall {work.get('overall')}/5 — {work.get('rationale')}")
            if work.get("strengths"):
                lines.append(f"  - Strengths: {'; '.join(work['strengths'])}")
            if work.get("weaknesses"):
                lines.append(f"  - Weaknesses: {'; '.join(work['weaknesses'])}")
        if r.get("deliverable"):
            preview_lines = r["deliverable"].splitlines()[:DELIVERABLE_PREVIEW_LINES]
            lines += ["", "  Deliverable preview:", "", "  ```"]
            lines += [f"  {ln}" for ln in preview_lines]
            if len(r["deliverable"].splitlines()) > DELIVERABLE_PREVIEW_LINES:
                lines.append("  ... (see deliverables/<archetype>.md for the full text)")
            lines.append("  ```")
        lines.append("")

    # ── generator improvement backlog ─────────────────────────────────────
    lines += ["## Generator improvement backlog", "", (
        "Every `skill_gaps` item the work judge returned, grouped by "
        "archetype. Gaps whose normalized text recurs in 2+ archetypes are "
        "promoted to the systemic list below — they point at a generator "
        "fix that would help across roles, not just one scenario."
    ), ""]

    gaps_by_archetype: dict[str, list[str]] = {}
    norm_to_archetypes: dict[str, set[str]] = defaultdict(set)
    norm_to_text: dict[str, str] = {}
    for r in ok:
        work = r.get("work_judge") or {}
        gaps = work.get("skill_gaps") or []
        if gaps:
            gaps_by_archetype[r["archetype"]] = gaps
        for g in gaps:
            key = _norm_gap(g)
            norm_to_archetypes[key].add(r["archetype"])
            norm_to_text.setdefault(key, g)

    systemic = [
        (norm_to_text[k], sorted(archs))
        for k, archs in norm_to_archetypes.items() if len(archs) >= 2
    ]
    lines += ["### Systemic gaps (2+ archetypes)", ""]
    if systemic:
        for text, archs in sorted(systemic, key=lambda x: -len(x[1])):
            lines.append(f"- {text} _(seen in: {', '.join(archs)})_")
    else:
        lines.append("- (none — no gap text recurred verbatim across archetypes)")
    lines.append("")

    lines += ["### Per-archetype gaps", ""]
    if gaps_by_archetype:
        for archetype, gaps in gaps_by_archetype.items():
            lines.append(f"**{archetype}**")
            lines += [f"- {g}" for g in gaps]
            lines.append("")
    else:
        lines.append("_(no skill_gaps recorded — likely run with --skip-exec, or no work judge succeeded)_")
        lines.append("")

    # ── hard failures ──────────────────────────────────────────────────────
    if errored:
        lines += ["## Hard failures", ""]
        for r in errored:
            lines.append(f"- {r.get('archetype')}: {r.get('error')}")
        lines.append("")
    harness_errors = [r for r in ok if (r.get("harness") or {}).get("status") in
                      ("harness_error", "harness_timeout")]
    if harness_errors:
        lines += ["## Harness failures (claude CLI)", ""]
        for r in harness_errors:
            h = r["harness"]
            lines.append(f"- {r.get('archetype')}: {h.get('status')} "
                         f"(rc={h.get('returncode')}) — {h.get('stderr_tail') or ''}")
        lines.append("")

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines))
    return md_path
