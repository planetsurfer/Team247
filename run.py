import argparse, json
import concurrent.futures as cf

import framework, gap
from agent import generate_agent, ANCHOR
from assess import assess_skill
from config import make_runner, MAX_PARALLEL, TARGET_COVERAGE, MAX_ROUNDS

def load_seed_battery(role_codes):
    items = json.load(open("data/battery_seed.json"))
    kept = [i for i in items if i["code"] in role_codes]   # §0.5 bug 1: seed ∩ role's own codes
    if not kept:
        raise SystemExit("Seed battery shares no skill codes with this role — "
                         "refusing to fabricate a score.")
    return kept

def assess_round(runner, battery, spec, label):
    with cf.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:   # one sandbox per skill
        results = list(ex.map(lambda it: assess_skill(runner, it, spec), battery))
    report = gap.gap_report(results)
    gap.print_report(report, label)
    return report

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default=framework.DEMO_ROLE)
    ap.add_argument("--task", help="Mode B: free-text task (stretch)")
    ap.add_argument("--generate", action="store_true",
                    help="use LLM-generated grounded battery instead of the seed")
    ap.add_argument("--instructions", default="",
                    help="requester's custom instructions")
    args = ap.parse_args()

    open("events.jsonl", "w").close()   # fresh lifecycle log per run — no stale events

    role_query = args.role
    if args.task:                                          # stretch C (Phase 10)
        import classify
        recs = classify.recommend_roles(args.task)
        role_query = recs[0]["role"]
        print(f"Task classified -> {role_query} "
              f"(confidence {recs[0]['confidence']}, matched on {recs[0]['matched_on']})")

    sector, track, role = framework.resolve_role(role_query)
    print(f"Role: {role}  [{sector} / {track}]  — source: SkillsFuture SFw")
    skills = framework.get_skills(role)
    context = framework.get_context(role)
    executable = framework.select_executable(skills)
    print(f"{len(skills)} official skills; {len(executable)} executable "
          f"(rest are rubric-track)")

    if args.generate:                                      # stretch A (Phase 8)
        import battery as batt
        bat = batt.build_battery(role, executable, context)
    else:
        bat = load_seed_battery({s["code"] for s in skills})
    if not bat:      # empty battery -> gap_report([]) would fabricate 100% coverage
        raise SystemExit("Battery is empty — refusing to fabricate a score.")
    print(f"Battery: {len(bat)} graded tasks")

    spec = generate_agent(role, skills, context, args.instructions, sector)
    if ANCHOR not in spec or len(spec) < 400:              # §8 fallback: agent_seed.md
        print("agent-gen fallback -> data/agent_seed.md")
        spec = open("data/agent_seed.md").read()
    open("agent_v0.md", "w").write(spec)

    runner = make_runner()
    rounds = [assess_round(runner, bat, spec, "baseline (spec v0)")]

    import refine
    rnd = 0
    while (rounds[-1]["coverage_pct"] < TARGET_COVERAGE
           and rnd < MAX_ROUNDS - 1 and rounds[-1]["gaps"]):
        rnd += 1
        print(f"\nRefining spec (round {rnd}) — patching '{ANCHOR}' ...")
        spec = refine.patch_agent(spec, rounds[-1]["gaps"])
        rounds.append(assess_round(runner, bat, spec, f"refined (spec v{rnd})"))

    open("agent_final.md", "w").write(spec)
    from battery import rubric_score
    exec_codes = {s["code"] for s in executable}
    rubric_skills, cov, tot = [], 0, 0
    non_exec = [x for x in skills if x["code"] not in exec_codes]
    judged = non_exec[:6]                                   # cap for time — disclosed below
    if len(judged) < len(non_exec):
        print(f"Rubric: judging {len(judged)}/{len(non_exec)} non-exec skills (capped for time)")
    for s in judged:
        ka = framework.get_ka(s["code"], s["required_level"])
        try:
            r = rubric_score(s["skill"], s["required_level"], ka, spec)
        except Exception as e:                              # never let the judge kill a finished run
            print(f"rubric: skipped '{s['skill']}' ({type(e).__name__})")
            continue
        if r:
            rubric_skills.append(r)
            cov += r["covered"]; tot += r["total"]
    rubric = ({"skills": rubric_skills, "covered": cov, "total": tot,
               "pct": round(100 * cov / tot, 1)} if tot else None)
    out = {"role": role, "sector": sector, "track": track, "rubric": rubric,
           "rounds": [{"coverage_pct": r["coverage_pct"],
                       "results": [{k: v for k, v in res.items() if k != "task_prompt"}
                                   for res in r["results"]]}
                      for r in rounds]}
    json.dump(out, open("output.json", "w"), indent=2)
    print(f"\nBASELINE: {rounds[0]['coverage_pct']}% -> FINAL: {rounds[-1]['coverage_pct']}%"
          f"  ({len(rounds)} round{'s' if len(rounds) > 1 else ''})")
    print("Saved: agent_final.md · output.json · events.jsonl")
    if rubric:
        print(f"\nHEADLINE — Executed: {rounds[-1]['coverage_pct']}% of level-points "
              f"· Rubric: {rubric['covered']}/{rubric['total']} K&A items "
              f"({rubric['pct']}%) — two numbers, NEVER blended")

if __name__ == "__main__":
    main()
