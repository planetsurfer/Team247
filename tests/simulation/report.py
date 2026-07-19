"""Aggregate conversations.jsonl into a markdown report (also --report-only)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, quantiles

from tests.simulation.judge import SCORE_DIMS

WORST_N = 10


def _load(jsonl_path: Path) -> list[dict]:
    records = []
    for line in jsonl_path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # Keep only the latest record per (persona, track) — resume may retry errors.
    latest: dict[tuple, dict] = {}
    for r in records:
        latest[(r.get("persona_id"), r.get("track"))] = r
    return list(latest.values())


def _fmt(x, nd=2):
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "—"


def _score_means(records: list[dict]) -> dict[str, float | None]:
    out = {}
    for dim in SCORE_DIMS:
        vals = [r["judge"]["scores"].get(dim) for r in records
                if r.get("judge") and isinstance(r["judge"]["scores"].get(dim), int)]
        out[dim] = mean(vals) if vals else None
    vals = [r["judge"]["overall"] for r in records if r.get("judge")]
    out["overall"] = mean(vals) if vals else None
    return out


def _sector_mismatch(records: list[dict]) -> tuple[float | None, float | None]:
    """Deterministic sector signal, no judge involved.

    Returns (agent_rate, team_rate): fraction of agents whose catalog sector
    differs from the persona's sector, and fraction of teams containing ZERO
    same-sector agents. Cross-sector roles are often legitimate (finance/ops
    cut across industries), so this is a diagnostic to compare across runs,
    not a pass/fail.
    """
    agents_total = agents_mismatched = 0
    teams_total = teams_all_foreign = 0
    for r in records:
        agents = (r.get("team") or {}).get("agents") or []
        sectors = [a.get("sector") for a in agents if a.get("sector")]
        if not sectors:
            continue
        agents_total += len(sectors)
        agents_mismatched += sum(s != r["sector"] for s in sectors)
        teams_total += 1
        teams_all_foreign += all(s != r["sector"] for s in sectors)
    return (
        agents_mismatched / agents_total if agents_total else None,
        teams_all_foreign / teams_total if teams_total else None,
    )


def _function_coverage(records: list[dict]) -> dict | None:
    """Deterministic per-function recall, no judge involved.

    Only over records that carry the Phase B functions_needed/
    functions_uncovered pair (older runs predate this and are skipped
    entirely — returns None if none of the records have the key).
    """
    have = [r for r in records if "functions_needed" in r]
    if not have:
        return None
    covered = sum(1 for r in have if not (r.get("functions_uncovered") or []))
    distinct_ids: set[str] = set()
    uncovered_counter: Counter = Counter()
    needed_counts: list[int] = []
    for r in have:
        needed = r.get("functions_needed") or []
        needed_counts.append(len(needed))
        for f in needed:
            if isinstance(f, dict) and f.get("id"):
                distinct_ids.add(f["id"])
        for f in r.get("functions_uncovered") or []:
            if isinstance(f, dict) and f.get("id"):
                uncovered_counter[f["id"]] += 1
    return {
        "n": len(have),
        "covered_rate": covered / len(have),
        "distinct_functions": len(distinct_ids),
        "mean_functions_needed": mean(needed_counts) if needed_counts else 0.0,
        "top_uncovered": uncovered_counter.most_common(10),
    }


def write_report(jsonl_path: Path, md_path: Path, config_echo: str = "") -> Path:
    records = _load(jsonl_path)
    ok = [r for r in records if r.get("status") == "ok"]
    judged = [r for r in ok if r.get("judge")]

    lines = [
        "# Team247 human-user simulation report",
        "",
        f"Source: `{jsonl_path}` — {len(records)} conversations "
        f"({len(ok)} ok, {sum(r['status'] == 'error' for r in records)} error, "
        f"{sum(r['status'] == 'timeout' for r in records)} timeout).",
        "",
        "> **Bias note:** the LLM judge shares a model with the system under test "
        "(qwen3.7-max unless `LLM_MODEL_SIM_JUDGE` overrides it); expect some "
        "self-judging leniency. The structural pass is deterministic and unbiased.",
        "",
    ]
    if config_echo:
        lines += [f"Run config: {config_echo}", ""]

    # ── overall ────────────────────────────────────────────────────────────
    structural_pass = [r for r in ok if r.get("structural", {}).get("passed")]
    lines += ["## Overall", ""]
    if ok:
        lines.append(f"- Structural pass rate: **{len(structural_pass)}/{len(ok)}** "
                     f"({100 * len(structural_pass) / len(ok):.0f}%)")
    means = _score_means(judged)
    for dim in (*SCORE_DIMS, "overall"):
        lines.append(f"- Mean {dim}: **{_fmt(means[dim])}**")
    agent_mm, team_mm = _sector_mismatch(ok)
    if agent_mm is not None:
        lines.append(f"- Sector mismatch (deterministic): **{100 * agent_mm:.0f}%** of "
                     f"agents from a different sector than the persona; "
                     f"**{100 * team_mm:.0f}%** of teams with zero same-sector agents")
    for track in sorted({r["track"] for r in records}):
        tr = [r for r in judged if r["track"] == track]
        lines.append(f"- Mean overall ({track} track, n={len(tr)}): "
                     f"**{_fmt(_score_means(tr)['overall'])}**")
    lines.append("")

    # ── function coverage (Phase B recall, deterministic) ───────────────────
    lines += ["## Function coverage", "",
              "Deterministic recall signal (no judge): does the candidate slate "
              "structurally cover every business function the task decomposed into? "
              "(`functions_needed` from `identify_functions`, `functions_uncovered` "
              "= needed functions with zero seed roles in `role_functions`.)", ""]
    fc = _function_coverage(ok)
    if fc is None:
        lines += ["_No records carry `functions_needed` (run predates Phase B)._", ""]
    else:
        lines += [
            f"- Records with function data: **{fc['n']}**",
            f"- Team-level recall (all needed functions covered): "
            f"**{100 * fc['covered_rate']:.0f}%**",
            f"- Distinct functions identified across run: **{fc['distinct_functions']}**",
            f"- Mean functions_needed per task: **{_fmt(fc['mean_functions_needed'])}**",
            "",
            "Most common uncovered functions (taxonomy/tagging gaps to patch next):",
            "",
        ]
        lines += [f"- `{fid}`: {n}" for fid, n in fc["top_uncovered"]] or ["- (none)"]
        lines.append("")

    # ── per-sector ─────────────────────────────────────────────────────────
    by_sector: dict[str, list[dict]] = defaultdict(list)
    for r in ok:
        by_sector[r["sector"]].append(r)
    lines += ["## Per sector", "",
              "| Sector | n | Structural pass | Mean overall | Top issues |",
              "|---|---|---|---|---|"]
    def _sector_key(item):
        recs = [r for r in item[1] if r.get("judge")]
        m = _score_means(recs)["overall"]
        return m if m is not None else 99
    for sector, recs in sorted(by_sector.items(), key=_sector_key):
        judged_s = [r for r in recs if r.get("judge")]
        passed = sum(r.get("structural", {}).get("passed", False) for r in recs)
        issues = Counter(t for r in judged_s for t in r["judge"].get("issues", []))
        top = ", ".join(f"{t}×{n}" for t, n in issues.most_common(3)) or "—"
        lines.append(f"| {sector} | {len(recs)} | {passed}/{len(recs)} | "
                     f"{_fmt(_score_means(judged_s)['overall'])} | {top} |")
    lines.append("")

    # ── failure taxonomy ───────────────────────────────────────────────────
    all_issues = Counter(t for r in judged for t in r["judge"].get("issues", []))
    struct_fails = Counter(e for r in ok for e in r.get("structural", {}).get("errors", []))
    lines += ["## Failure taxonomy", "", "LLM-judge issue tags:", ""]
    lines += [f"- `{t}`: {n}" for t, n in all_issues.most_common()] or ["- (none)"]
    lines += ["", "Structural check failures:", ""]
    lines += [f"- `{t}`: {n}" for t, n in struct_fails.most_common()] or ["- (none)"]
    hard = [r for r in records if r["status"] != "ok"]
    if hard:
        lines += ["", "Hard failures (error/timeout):", ""]
        lines += [f"- {r['persona_id']}/{r['track']}: {r['status']} — {r.get('error')}"
                  for r in hard]
    lines.append("")

    # ── worst conversations ────────────────────────────────────────────────
    worst = sorted(judged, key=lambda r: r["judge"]["overall"])[:WORST_N]
    lines += [f"## Worst {min(WORST_N, len(worst))} conversations", ""]
    for r in worst:
        j = r["judge"]
        lines += [
            f"### {r['persona_id']} ({r['track']}) — overall {j['overall']}/5",
            f"- Task: {r['persona']['task_utterance']!r} "
            f"({r['persona']['occupation']}, {r['sector']})",
            f"- Team: {', '.join(a.get('role', '?') for a in r['team']['agents']) or '(empty)'}",
            f"- Issues: {', '.join(j.get('issues', [])) or '—'}",
            f"- Judge: {j['rationale']}",
            "",
        ]

    # ── latency ────────────────────────────────────────────────────────────
    secs = sorted(r["timing"]["seconds"] for r in records if r.get("timing", {}).get("seconds"))
    if len(secs) >= 4:
        q = quantiles(secs, n=4)
        lines += ["## Latency (per conversation, seconds)", "",
                  f"- p25 {q[0]:.0f} / p50 {q[1]:.0f} / p75 {q[2]:.0f} / "
                  f"max {secs[-1]:.0f}", ""]

    md_path.write_text("\n".join(lines))
    return md_path
