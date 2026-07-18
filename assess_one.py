"""Phase 2 DoD: assess ONE seed skill end-to-end (spec-as-system-prompt) — repeatable."""
import json, sys

from config import make_runner
from assess import assess_skill

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
item = json.load(open("data/battery_seed.json"))[idx]
spec = open("data/agent_seed.md").read()
r = assess_skill(make_runner(), item, spec)
print({k: r[k] for k in ("skill", "score", "held_level", "gap", "error", "seconds")})
print(f'GRADE:{{"score": {r["score"]}}}')
