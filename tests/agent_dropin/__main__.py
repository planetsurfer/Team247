"""CLI for the agent drop-in eval.

    python -m tests.agent_dropin --archetypes credit_ops
    python -m tests.agent_dropin --resume
    python -m tests.agent_dropin --report-only --out sim_results/dropin-20260720-100000
    python -m tests.agent_dropin --reuse-teams sim_results/dropin-full2/results.jsonl
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import subprocess
import sys
import threading
from pathlib import Path

from tests.agent_dropin import harness as harness_mod
from tests.agent_dropin import report as report_mod
from tests.agent_dropin import scenarios as scenarios_mod


def _completed_archetypes(jsonl_path: Path) -> set[str]:
    """Archetype ids with a status="ok" record already on disk — errors are
    always retried on --resume, mirroring tests/simulation/runner.py."""
    done = set()
    if jsonl_path.exists():
        for line in jsonl_path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("status") == "ok":
                done.add(rec.get("archetype"))
    return done


def _load_reuse_team_ids(jsonl_path: Path) -> dict[str, str]:
    """archetype id -> team_id, resolved from a prior run's results.jsonl for
    --reuse-teams. Only status="ok" records (which have a `team` block) are
    considered; a later line for the same archetype overwrites an earlier one
    (a jsonl grows via --resume, so the last line for an archetype is its
    most current outcome — same "last wins" spirit as _completed_archetypes).

    Raises FileNotFoundError if jsonl_path doesn't exist — an explicitly
    named --reuse-teams source that's missing is a usage error, not something
    to silently degrade from.
    """
    if not jsonl_path.exists():
        raise FileNotFoundError(f"--reuse-teams file not found: {jsonl_path}")
    out: dict[str, str] = {}
    for line in jsonl_path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("status") != "ok":
            continue
        archetype = rec.get("archetype")
        team_id = (rec.get("team") or {}).get("team_id")
        if archetype and team_id:
            out[archetype] = team_id
    return out


def _preflight_claude(claude_bin: str) -> bool:
    try:
        cp = subprocess.run([claude_bin, "--version"], capture_output=True,
                            text=True, timeout=15)
        return cp.returncode == 0
    except Exception:  # noqa: BLE001 — binary missing, not executable, etc.
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tests.agent_dropin",
        description="Generate SKILL.md bundles for 5 agent archetypes, run "
                    "them headless via the claude CLI on a realistic "
                    "scenario, and judge comprehensiveness + work product.",
    )
    ap.add_argument("--archetypes", default=None,
                    help="comma-separated archetype ids (default: all 5)")
    ap.add_argument("--out", default=None,
                    help="output dir (default sim_results/dropin-<timestamp>)")
    ap.add_argument("--resume", action="store_true",
                    help="skip archetypes already status=ok in the out dir's results.jsonl")
    ap.add_argument("--report-only", action="store_true",
                    help="just rebuild report.md from the out dir's results.jsonl")
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--claude-model", default="sonnet")
    ap.add_argument("--timeout", type=int, default=300,
                    help="per-archetype claude CLI subprocess timeout, seconds")
    ap.add_argument("--skip-exec", action="store_true",
                    help="skip the claude CLI run entirely — comprehensiveness "
                        "judge only, no claude binary or work judge involved")
    ap.add_argument("--force-regen", action="store_true",
                    help="bypass the base-skill/task-overlay LLM generation caches")
    ap.add_argument("--reuse-teams", default=None, metavar="RESULTS_JSONL",
                    help="path to a prior run's results.jsonl; for each archetype, "
                        "reuse that archetype's team_id (same staffing + wiring) "
                        "instead of calling team_service.recommend + "
                        "handoff_service.wire again — a paired generator A/B on "
                        "the SAME team/wiring. Archetypes with no matching "
                        "status=ok record in the source file record status=error.")
    args = ap.parse_args(argv)

    if args.out:
        out_dir = Path(args.out)
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path("sim_results") / f"dropin-{stamp}"
    jsonl_path = out_dir / "results.jsonl"

    if args.report_only:
        if not jsonl_path.exists():
            # Convenience: with no --out, fall back to the newest run dir.
            runs = sorted(Path("sim_results").glob("dropin-*/results.jsonl"))
            if not runs:
                print("no results.jsonl found — pass --out <run dir>", file=sys.stderr)
                return 2
            jsonl_path = runs[-1]
            out_dir = jsonl_path.parent
        md = report_mod.write_report(jsonl_path, out_dir / "report.md")
        print(f"[report] {md}")
        return 0

    ids = ([s.strip() for s in args.archetypes.split(",") if s.strip()]
          if args.archetypes else list(scenarios_mod.BY_ID))
    unknown = [i for i in ids if i not in scenarios_mod.BY_ID]
    if unknown:
        print(f"unknown archetype id(s): {unknown} — choose from "
             f"{list(scenarios_mod.BY_ID)}", file=sys.stderr)
        return 2
    to_run = [scenarios_mod.BY_ID[i] for i in ids]

    reuse_team_ids: dict[str, str] = {}
    if args.reuse_teams:
        try:
            reuse_team_ids = _load_reuse_team_ids(Path(args.reuse_teams))
        except FileNotFoundError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 2
        missing = [s.id for s in to_run if s.id not in reuse_team_ids]
        if missing:
            print(f"[warn] --reuse-teams {args.reuse_teams}: no status=ok team_id "
                 f"found for archetype(s) {missing} — those will record "
                 "status=error", file=sys.stderr)

    if not args.skip_exec and not _preflight_claude(args.claude_bin):
        print(f"[warn] `{args.claude_bin} --version` failed — downgrading this "
             "run to --skip-exec (comprehensiveness judge only, no claude CLI "
             "execution or work judge)", file=sys.stderr)
        args.skip_exec = True

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "skills").mkdir(exist_ok=True)
    (out_dir / "deliverables").mkdir(exist_ok=True)
    run_id = out_dir.name

    done = _completed_archetypes(jsonl_path) if args.resume else set()
    work = [s for s in to_run if s.id not in done]
    print(f"[run] {len(work)} archetypes to do ({len(to_run) - len(work)} "
         f"already complete) -> {jsonl_path}")

    stop_flag = threading.Event()

    def _sigint(_sig, _frm):
        if stop_flag.is_set():
            raise KeyboardInterrupt  # second Ctrl-C: bail hard
        print("\n[run] Ctrl-C — finishing in-flight archetype then stopping "
             "(press again to abort)")
        stop_flag.set()

    old_handler = signal.signal(signal.SIGINT, _sigint)
    n_done = 0
    try:
        with open(jsonl_path, "a", encoding="utf-8") as fh:
            for scenario in work:
                if stop_flag.is_set():
                    break
                record = harness_mod.run_archetype(
                    scenario, args, run_id,
                    reuse_team_id=reuse_team_ids.get(scenario.id),
                    reuse_requested=bool(args.reuse_teams),
                )

                skill_md = record.pop("_skill_md", None)
                if skill_md:
                    (out_dir / "skills" / f"{scenario.id}.SKILL.md").write_text(skill_md)
                if record.get("deliverable"):
                    (out_dir / "deliverables" / f"{scenario.id}.md").write_text(
                        record["deliverable"])

                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                n_done += 1

                comp = (record.get("comp_judge") or {}).get("overall")
                work_score = (record.get("work_judge") or {}).get("overall")
                harness_status = (record.get("harness") or {}).get("status")
                print(
                    f"[{n_done}/{len(work)}] {scenario.id}: {record['status']}"
                    + (f", comp={comp}, work={work_score}, harness={harness_status}"
                      if record["status"] == "ok" else f" ({record.get('error')})")
                )
    finally:
        signal.signal(signal.SIGINT, old_handler)

    config_echo = (
        f"archetypes={','.join(s.id for s in to_run)}, skip_exec={args.skip_exec}, "
        f"force_regen={args.force_regen}, reuse_teams={args.reuse_teams}, "
        f"claude_bin={args.claude_bin}, "
        f"claude_model={args.claude_model}, timeout={args.timeout}"
    )
    md = report_mod.write_report(jsonl_path, out_dir / "report.md", config_echo)
    print(f"[report] {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
