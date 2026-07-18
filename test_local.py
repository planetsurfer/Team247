from assess import parse_grade, held_level

assert parse_grade('noise\nGRADE:{"score": 0.75}')[0] == 0.75
assert parse_grade('GRADE:{"score": 2.0}')[0] == 1.0          # clamped
assert parse_grade('GRADE:{"score": NaN}')[0] == 0.0          # non-finite is invalid
assert parse_grade('GRADE:{"score": "Infinity"}')[0] == 0.0
assert parse_grade("no grade here") == (0.0, "no GRADE line in output")
assert parse_grade("GRADE:{bad json}")[0] == 0.0
assert held_level(0.7, 3) == 3
assert held_level(0.4, 3) == 2
assert held_level(0.1, 3) == 1
assert held_level(0.1, 1) == 0                                 # floor at 0
print("LOCAL TESTS OK")

from agent import ANCHOR
import refine
spec = f"# X\n\nbody\n\n{ANCHOR}\n\n_None yet._\n"
p1 = refine.patch_agent(spec, [])            # no gaps -> no LLM call
assert ANCHOR in p1 and "_None yet._" not in p1
p2 = refine.patch_agent(p1 + "\n### Old guidance\nkeep me", [])
assert "keep me" in p2                       # a second pass never destroys prior guidance
print("ANCHOR TESTS OK")

import framework
assert framework.resolve_role("Data Engineer")[2] == "Data Engineer"
assert framework.resolve_role("Data Analyst")[2] == framework.DEMO_ROLE
print("ROLE RESOLUTION TESTS OK")
