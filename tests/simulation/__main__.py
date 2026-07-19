"""CLI for the human-user simulation harness.

    python -m tests.simulation --personas 100 --track both --resume
    python -m tests.simulation --report-only --out sim_results/run-20260719-100000
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from tests.simulation import personas as personas_mod
from tests.simulation import report as report_mod
from tests.simulation import runner as runner_mod
from tests.simulation.client import SimClient


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tests.simulation",
        description="Simulate human users against the live Team247 server and "
                    "judge the recommended teams.",
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--personas", type=int, default=100,
                    help="how many personas to run (seeded sector-balanced subsample)")
    ap.add_argument("--sectors", default=None,
                    help="comma-separated sector filter (default: all)")
    ap.add_argument("--track", choices=("intake", "oneshot", "both"), default="both")
    ap.add_argument("--out", default=None,
                    help="output dir (default sim_results/run-<timestamp>)")
    ap.add_argument("--resume", action="store_true",
                    help="skip (persona, track) pairs already in the out dir's JSONL")
    ap.add_argument("--regen-personas", action="store_true",
                    help="regenerate personas.json instead of using the cache")
    ap.add_argument("--report-only", action="store_true",
                    help="just rebuild report.md from the out dir's JSONL")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="parallel conversations (2-3 safe with RATE_LIMIT_PER_MIN raised)")
    ap.add_argument("--target-rpm", type=float, default=8.0,
                    help="client-side pacing for rate-limited endpoints")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    if args.out:
        out_dir = Path(args.out)
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path("sim_results") / f"run-{stamp}"

    jsonl_path = out_dir / "conversations.jsonl"
    if args.report_only:
        if not jsonl_path.exists():
            # Convenience: with no --out, fall back to the newest run dir.
            runs = sorted(Path("sim_results").glob("run-*/conversations.jsonl"))
            if not runs:
                print("no conversations.jsonl found — pass --out <run dir>", file=sys.stderr)
                return 2
            jsonl_path = runs[-1]
            out_dir = jsonl_path.parent
        md = report_mod.write_report(jsonl_path, out_dir / "report.md")
        print(f"[report] {md}")
        return 0

    client = SimClient(args.base_url, target_rpm=args.target_rpm)
    if not client.health_ok():
        print(f"server not reachable at {args.base_url} — start it first, e.g.\n"
              f"  RATE_LIMIT_PER_MIN=60 uvicorn app.main:app", file=sys.stderr)
        return 2

    sectors_filter = [s.strip() for s in args.sectors.split(",")] if args.sectors else None
    people = personas_mod.get_personas(
        client, n=args.personas, sectors_filter=sectors_filter,
        regen=args.regen_personas, seed=args.seed,
    )
    if not people:
        print("no personas matched the filter", file=sys.stderr)
        return 2
    tracks = ["intake", "oneshot"] if args.track == "both" else [args.track]
    print(f"[run] {len(people)} personas × {tracks} → {out_dir} "
          f"(rpm={args.target_rpm}, concurrency={args.concurrency})")

    runner_mod.run(client, people, tracks, out_dir,
                   resume=args.resume, concurrency=args.concurrency)

    config_echo = (f"base_url={args.base_url}, personas={len(people)}, "
                   f"tracks={'+'.join(tracks)}, rpm={args.target_rpm}, "
                   f"concurrency={args.concurrency}, seed={args.seed}, "
                   f"client_stats={client.stats}")
    md = report_mod.write_report(jsonl_path, out_dir / "report.md", config_echo)
    print(f"[report] {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
