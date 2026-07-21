# Team247 Closed-Beta Runbook

Live at **https://team247.io**. One box, one operator surface: SSM. There is no
SSH key for this instance — all access is via SSM Session Manager / `send-command`.

| Thing | Value |
|---|---|
| Instance | `i-0329f8a02a51b4130` (t4g.small, AL2023 arm64) |
| Region | `ap-southeast-1` |
| Elastic IP | `54.169.207.229` |
| Security group | `sg-05370bc1aa9090e19` |
| Repo on box | `/opt/team247` (branch `feat/per-agent-specs-ka-guidance`) |
| CloudWatch log group | `/team247/beta` |
| SSM param prefix | `/team247/prod/*` |

All AWS CLI commands below assume `--region ap-southeast-1` (either pass it
explicitly, as shown, or `export AWS_DEFAULT_REGION=ap-southeast-1` once).

---

## 1. Architecture map

Route 53 resolves `team247.io` / `www.team247.io` to the Elastic IP. Caddy
(`deploy/docker-compose.caddy.yml`, container `team247-caddy`, `network_mode:
host`) terminates TLS on :443 with an auto-issued/renewed Let's Encrypt cert
(`deploy/Caddyfile`) and reverse-proxies to the app, which is bound to
`127.0.0.1:8000` only (`docker-compose.prod.yml`) — nothing but Caddy can
reach it from outside the box. The app container (`team247-prod`) runs as
non-root uid 10001, read-only root FS (tmpfs `/tmp`), and stores its SQLite
DB (WAL mode) on the EBS root volume under `./data` (bind-mounted to
`/app/data`). Secrets (`LLM_API_KEY`, `APP_ADMIN_TOKEN`, `BETA_TOKEN_SALT`,
`LLM_MODEL`) live in SSM Parameter Store under `/team247/prod/*` and are
pulled into `deploy/secrets.env` (gitignored, chmod 600, never committed) by
`deploy/fetch_secrets.sh`, which the app's `env_file:` reads at container
start. Container logs ship to CloudWatch Logs group `/team247/beta` via the
`awslogs` driver (`deploy/docker-compose.aws.yml` overlay). A DLM policy
snapshots the tagged (`Name=team247-beta`) EBS volume daily at 12:00 UTC,
retaining 7. The security group allows only 80/443 inbound from `0.0.0.0/0`
(80 redirects to 443 in Caddy); there is no SSH ingress — box access is only
via SSM Session Manager, using the instance's IAM role (`team247-beta-ec2`,
scoped to `ssm:GetParameter` on `/team247/prod/*` and `logs:PutLogEvents` on
`/team247/beta`).

Request path: `browser → :443 Caddy (TLS) → 127.0.0.1:8000 app (uvicorn)`.

---

## 2. Deploy / update procedure

Box is already provisioned (`deploy/infra.sh` — re-run only if a resource
needs recreating; it's idempotent). To ship a new commit on
`feat/per-agent-specs-ka-guidance`:

```bash
CMD_ID=$(aws ssm send-command \
  --region ap-southeast-1 \
  --instance-ids i-0329f8a02a51b4130 \
  --document-name "AWS-RunShellScript" \
  --comment "team247 deploy: git pull + compose up -d --build" \
  --parameters commands='[
    "cd /opt/team247",
    "sudo git pull --ff-only",
    "sudo docker compose -f docker-compose.prod.yml -f deploy/docker-compose.aws.yml up -d --build",
    "sudo docker compose -f deploy/docker-compose.caddy.yml up -d",
    "sleep 5",
    "curl -sf http://127.0.0.1:8000/api/health || echo HEALTHCHECK_FAILED"
  ]' \
  --query 'Command.CommandId' --output text)
echo "$CMD_ID"

# poll for completion / read output
aws ssm get-command-invocation \
  --region ap-southeast-1 \
  --instance-id i-0329f8a02a51b4130 \
  --command-id "$CMD_ID" \
  --query '{Status:Status,Out:StandardOutputContent,Err:StandardErrorContent}'
```

`git pull --ff-only` fails loudly (non-zero) if the box has diverged from
`origin` — never force-pushes or resets on the box.

**Watch boot / bootstrap logs** (first boot runs `deploy/user-data.sh` as
root, logging to `/var/log/team247-bootstrap.log`; useful after a
replacement instance launch or to debug a stuck deploy):

```bash
CMD_ID=$(aws ssm send-command \
  --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130 \
  --document-name "AWS-RunShellScript" \
  --parameters commands='["tail -n 200 /var/log/team247-bootstrap.log"]' \
  --query 'Command.CommandId' --output text)
aws ssm get-command-invocation --region ap-southeast-1 \
  --instance-id i-0329f8a02a51b4130 --command-id "$CMD_ID" \
  --query 'StandardOutputContent' --output text
```

**Interactive shell** (Session Manager, no SSH key needed):

```bash
aws ssm start-session --region ap-southeast-1 --target i-0329f8a02a51b4130
```

---

## 3. Beta token ops

Minted/listed/revoked via `app.mint_token`, run **inside the running app
container** on the box (it shares the container's `/app/data` DB via the
bind mount, and prod always runs `BETA_AUTH=on` — that setting doesn't
affect the CLI, only the FastAPI auth dependency):

```bash
# mint (label required; --total = lifetime cap, --quota = daily cap; omit either for unlimited)
sudo docker exec team247-prod python -m app.mint_token "acme-corp-beta" --total 200

# list — hash_prefix, label, active, daily=<n|unl>, total=<used>/<n|unl>, created, last_used
sudo docker exec team247-prod python -m app.mint_token --list

# revoke by label or hash prefix
sudo docker exec team247-prod python -m app.mint_token --revoke "acme-corp-beta"
```

The plaintext token is printed **once**, immediately after minting — the DB
only stores its salted SHA-256 hash (`BETA_TOKEN_SALT`); there is no way to
recover it later, so capture it from the SSM command output before closing
the session. Usage (per-day and lifetime counts) lives in `beta_token_usage`
(summed per token in `--list`'s `total=<used>/<n>` column); `auth.list_tokens()`
in `app/auth.py` is the single source of truth both the CLI and any future
admin UI should read from.

---

### 3b. Feedback + full beta proving (2026-07-21)
- Feedback: `docker exec team247-prod python -m app.feedback --list [--recent N]` / `--stats` — one row per (token, team), upserted.
- `/verify` now accepts BETA tokens (owner decision — full proving for testers), metered as one daily generation per prove; admin token remains a superset. The sandbox subprocess runs with a scrubbed env (no secrets inherited).

### 3c. Real-inputs intake — retention + purge (Iteration 3, 2026-07-21)

Users can paste their own real inputs (a price list, a policy, past letters)
via `PUT /api/team/{team_id}/inputs`; the raw text is stored verbatim in a
new nullable column, `teams.user_inputs` (JSON `[{kind, name, content}]`,
migration `0006_user_inputs.py`), and gets baked into that team's generated
SKILL.md — no separate table, no separate retention clock from the rest of
the `teams` row. Caps: <=5 items, <=100-char name, <=20KB total content per
team (enforced server-side, `app/services/team_service.py`
`_validate_user_inputs`).

There is no automatic expiry yet — this content lives exactly as long as the
`teams` row does. To purge one team's saved inputs without deleting the team
(e.g. a user asks you to remove what they pasted), run **inside the app
container** (same access pattern as §3's token ops):

```bash
sudo docker exec team247-prod python -c "
from app import db
db.execute('UPDATE teams SET user_inputs = NULL WHERE team_id = ?', ('<team_id>',))
"
```

To purge every team's saved inputs at once (does not touch `name`,
`use_case`, `brief`, or any other column — only `user_inputs`):

```bash
sudo docker exec team247-prod python -c "
from app import db
db.execute('UPDATE teams SET user_inputs = NULL WHERE user_inputs IS NOT NULL')
"
```

Deleting the team outright (`DELETE /api/team/{team_id}`, or
`DELETE FROM teams WHERE team_id = ?`) removes the inputs along with
everything else on that row — there's no separate cleanup step needed in
that case.

## 4. Admin token retrieval

```bash
aws ssm get-parameter --region ap-southeast-1 \
  --name /team247/prod/APP_ADMIN_TOKEN --with-decryption \
  --query 'Parameter.Value' --output text
```

To use it in the UI: visit `https://team247.io/?admin=1`, which surfaces the
admin-token bar (`web/src/components/AdminTokenBar.tsx`); paste the value
above and Save. It's stored client-side and sent as `Authorization: Bearer
<token>` — required for `/verify`, `/distill`, `--distill-all`, and it also
satisfies the beta-auth gate on every other LLM endpoint
(`app.settings.admin_token_ok`). The bar also reappears automatically if a
stored token gets rejected (401).

---

## 5. LLM key rotation

**The key currently deployed is the dev-era one (it was exposed in
terminals during development — see `PRODUCTION_ROADMAP.md` P0 #2).
Rotate it before widening the beta.**

```bash
# 1. write the new key to SSM (overwrite the existing SecureString)
aws ssm put-parameter --region ap-southeast-1 \
  --name /team247/prod/LLM_API_KEY --type SecureString \
  --value "<NEW_KEY>" --overwrite

# 2. on the box: re-fetch secrets.env, then restart the app to pick it up
CMD_ID=$(aws ssm send-command \
  --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130 \
  --document-name "AWS-RunShellScript" \
  --parameters commands='[
    "cd /opt/team247",
    "sudo bash deploy/fetch_secrets.sh",
    "sudo docker compose -f docker-compose.prod.yml -f deploy/docker-compose.aws.yml restart app"
  ]' \
  --query 'Command.CommandId' --output text)
aws ssm get-command-invocation --region ap-southeast-1 \
  --instance-id i-0329f8a02a51b4130 --command-id "$CMD_ID" \
  --query '{Status:Status,Out:StandardOutputContent,Err:StandardErrorContent}'
```

Never paste the new key into a shell history file or a non-SecureString
parameter. The same pattern (`put-parameter --overwrite` → `fetch_secrets.sh`
→ `restart app`) rotates `APP_ADMIN_TOKEN` and `BETA_TOKEN_SALT` too — note
rotating `BETA_TOKEN_SALT` invalidates every previously-minted beta token
(their hashes no longer match), so only do that alongside a planned re-mint.

---

## 6. Logs & health checks

```bash
# tail app container logs (shipped via awslogs driver)
aws logs tail /team247/beta --follow --region ap-southeast-1

# caddy logs — on the box (not shipped to CloudWatch)
aws ssm start-session --region ap-southeast-1 --target i-0329f8a02a51b4130
# then, in the session:
sudo docker logs -f team247-caddy
```

Health checks:

```bash
# public, through Caddy/TLS
curl -s https://team247.io/api/health
# -> {"ok":true,"roles_count":<int>,"ka_warmed":<bool>}

curl -s https://team247.io/api/auth/status
# -> {"beta_auth":true,"authenticated":<bool>}   (cheap, unauthenticated probe the UI polls)

# on-box, bypassing Caddy (via SSM send-command, one-liner)
aws ssm send-command --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130 \
  --document-name "AWS-RunShellScript" \
  --parameters commands='["curl -sf http://127.0.0.1:8000/api/health"]' \
  --query 'Command.CommandId' --output text
```

---

## 7. Snapshot restore

**DLM policy** (`team247-beta-daily-snapshot`) snapshots the EBS volume
tagged `Name=team247-beta` every 24h at 12:00 UTC, retaining the last 7.

Find snapshots:

```bash
aws ec2 describe-snapshots --region ap-southeast-1 --owner-ids self \
  --filters "Name=tag:Name,Values=team247-beta" \
  --query 'reverse(sort_by(Snapshots,&StartTime))[].{Id:SnapshotId,Time:StartTime,State:State}' \
  --output table
```

Restore into a new volume and swap it onto the (stopped) instance:

```bash
SNAP_ID="snap-XXXXXXXXXXXXXXXXX"     # pick from the list above
AZ=$(aws ec2 describe-instances --region ap-southeast-1 \
  --instance-ids i-0329f8a02a51b4130 \
  --query 'Reservations[0].Instances[0].Placement.AvailabilityZone' --output text)

NEW_VOL=$(aws ec2 create-volume --region ap-southeast-1 \
  --availability-zone "$AZ" --snapshot-id "$SNAP_ID" \
  --volume-type gp3 \
  --tag-specifications "ResourceType=volume,Tags=[{Key=Project,Value=Team247},{Key=Name,Value=team247-beta-restore}]" \
  --query 'VolumeId' --output text)
aws ec2 wait volume-available --region ap-southeast-1 --volume-ids "$NEW_VOL"

# stop the instance, detach the current root volume, attach the restored one, start
aws ec2 stop-instances --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130
aws ec2 wait instance-stopped --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130

OLD_VOL=$(aws ec2 describe-volumes --region ap-southeast-1 \
  --filters "Name=attachment.instance-id,Values=i-0329f8a02a51b4130" \
  --query 'Volumes[0].VolumeId' --output text)
aws ec2 detach-volume --region ap-southeast-1 --volume-id "$OLD_VOL"
aws ec2 wait volume-available --region ap-southeast-1 --volume-ids "$OLD_VOL"

aws ec2 attach-volume --region ap-southeast-1 \
  --volume-id "$NEW_VOL" --instance-id i-0329f8a02a51b4130 --device /dev/xvda

aws ec2 start-instances --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130
aws ec2 wait instance-running --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130
```

Keep `$OLD_VOL` around (don't delete) until the restore is confirmed good,
then `aws ec2 delete-volume --volume-id "$OLD_VOL"`.

**App-level alternative** (no downtime, DB-consistent, doesn't touch the
instance) — use SQLite's own `.backup` against the live WAL DB, then copy
the backup off-box:

```bash
aws ssm send-command --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130 \
  --document-name "AWS-RunShellScript" \
  --parameters commands='[
    "sudo docker exec team247-prod python -c \"import sqlite3; sqlite3.connect(\\\"/app/data/agentproof.db\\\").backup(sqlite3.connect(\\\"/app/data/agentproof.backup.db\\\"))\""
  ]' \
  --query 'Command.CommandId' --output text
# then pull /opt/team247/data/agentproof.backup.db off the box via a Session Manager
# file-transfer plugin, or aws s3 cp from within a send-command if an S3 bucket exists.
```

---

## 8. Rollback

**Code rollback** (bad deploy, DB schema unaffected):

```bash
aws ssm send-command --region ap-southeast-1 --instance-ids i-0329f8a02a51b4130 \
  --document-name "AWS-RunShellScript" \
  --parameters commands='[
    "cd /opt/team247",
    "sudo git checkout <prior-good-sha>",
    "sudo docker compose -f docker-compose.prod.yml -f deploy/docker-compose.aws.yml up -d --build",
    "sudo docker compose -f deploy/docker-compose.caddy.yml up -d",
    "curl -sf http://127.0.0.1:8000/api/health || echo HEALTHCHECK_FAILED"
  ]' \
  --query 'Command.CommandId' --output text
```

Redeploy the current branch tip afterward once the fix lands
(`git checkout feat/per-agent-specs-ka-guidance && git pull --ff-only`).

**Data rollback** (corrupted/bad DB state): use the snapshot-swap procedure
in §7 — stop instance, detach current volume (keep it, don't delete), attach
a prior daily snapshot's restored volume, start.

---

## 9. Emergency: close public ingress

If anything looks like it's exposing unauthenticated LLM spend or a security
issue, the fastest full stop is revoking SG ingress (kills 80/443 from the
internet; SSM access is unaffected since it doesn't go through the SG's
ingress rules):

```bash
aws ec2 revoke-security-group-ingress --region ap-southeast-1 \
  --group-id sg-05370bc1aa9090e19 \
  --ip-permissions \
    'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
    'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]'
```

Verify it's closed:

```bash
aws ec2 describe-security-groups --region ap-southeast-1 \
  --group-ids sg-05370bc1aa9090e19 --query 'SecurityGroups[0].IpPermissions'
# should be empty
```

To restore once the issue is resolved, re-run `deploy/infra.sh` (step `[b]`
re-authorizes the same two rules — it's idempotent, but skips re-creating
the SG since it already exists; you'd need to re-run just the
`authorize-security-group-ingress` call from that step, or re-add the two
`IpPermissions` blocks above via `aws ec2 authorize-security-group-ingress`).

**This never applies to auth** — beta/admin tokens stay in SSM regardless;
this is only for cutting network exposure, not for rotating credentials
(see §5 for that).

---

## 10. Starter gallery rebuild (Iteration 4, 2026-07-21)

The Landing page's "Start from a proven agent" gallery (5 fixed archetypes —
collections-chaser, contract-reviewer, quotation-writer,
onboarding-coordinator, campaign-planner) is served entirely from
`gallery_agents` (migration `0007_gallery_agents.py`): GET `/api/gallery`
(open, metadata only) and GET `/api/gallery/{slug}` (beta-gated, full row
incl. the composed `bundle_md`) never call an LLM — they only ever read that
table. All the LLM work happens offline, run **inside the running app
container** on the box (same access pattern as §3's token ops), via:

```bash
# build any archetype that doesn't have a row yet — idempotent, safe to
# re-run after every deploy; existing slugs are left untouched
sudo docker exec team247-prod python -m app.build_gallery

# rebuild everything (e.g. after a prompt/grounding change you want reflected
# in the gallery's pre-built bundles)
sudo docker exec team247-prod python -m app.build_gallery --force

# rebuild just one or two archetypes (repeatable, or comma-separated) —
# bounds LLM cost/time when only one archetype needs a refresh
sudo docker exec team247-prod python -m app.build_gallery --force --only collections-chaser
sudo docker exec team247-prod python -m app.build_gallery --force --only collections-chaser,quotation-writer
```

Each archetype runs the exact same pipeline a real user's first task does
(`team_service.recommend` -> `handoff_service.wire` (best-effort — a wiring
failure is reported and the build continues unwired) ->
`skill_bundle_service.compose_bundle` for the primary agent), so per-archetype
cost/latency matches a normal `/api/team/recommend` + `/wire` +
`/skill-bundles` sequence for that use case (real local timing:
~40-50s/archetype on `kimi-k2.6`, mostly the same LLM round trips a live user
triggers). One archetype failing (composer guardrail miss, transient LLM
error) is reported to stdout and skipped — it never aborts the remaining
archetypes; re-run the same command afterward to retry just the failed one
(it will rebuild since it never got a row).

There's no scheduled/automatic rebuild — run this by hand after a deploy that
changes the framework dataset, the base-skill/task-overlay prompts, or the
5 archetype definitions in `app/build_gallery.py`. Verify with:

```bash
curl -s https://team247.io/api/gallery
# -> [{"slug":...,"label":...,"blurb":...}, ...] — should list all 5
```

---

## 11. Battery top-20 population + gallery receipts (Iteration 6, 2026-07-22)

Two pieces, both offline/admin-path — neither is on the request hot path:

1. **Battery population** (`app/build_battery.py`) — populates
   `card_battery_items` (graded coding tasks + their sandbox-validated
   grader) for the roles that matter most: the top-N roles by `team_agents`
   usage, unioned with the 5 starter-gallery agents' roles (so the gallery
   always has something to verify against). Read by
   `verify_service.verify()`'s executed track and by
   `card_service.battery_items()`.
2. **Gallery receipts** — once a role has battery items AND an admin has run
   `verify_service.verify(team_id, agent_id)` for a gallery agent, GET
   `/api/gallery` / `/api/gallery/{slug}` automatically surface a receipts
   summary (`app/routers/gallery.py` + `verify_service.get_receipts`) — no
   separate step needed once both of the below have run.

Run both **inside the running app container** (same access pattern as §3/§10):

```bash
# 1) populate the battery for the top 20 roles (+ the 5 gallery roles, always
#    included) — idempotent: a role/skill with a 'ready' item is left alone
sudo docker exec team247-prod python -m app.build_battery

# bound cost further while iterating: only 1 skill per role instead of 3
sudo docker exec team247-prod python -m app.build_battery --top 20 --limit-skills 1

# rebuild everything (e.g. after a prompt/grounding change) — regenerates
# even roles/skills that already have a 'ready' item
sudo docker exec team247-prod python -m app.build_battery --force

# smoke-test / rebuild just one role (role_id from GET /api/catalog or the
# roles table) — bounds LLM + sandbox cost to a single role
sudo docker exec team247-prod python -m app.build_battery --only 42 --limit-skills 1
```

Cost/time expectations (real local timing on `kimi-k2.6`, LocalRunner
sandbox, updated 2026-07-22 after adding the retry loop below): **up to 3
generate+validate ATTEMPTS per skill** (`_generate_and_validate` in
`app/build_battery.py`), each attempt being 1 LLM call
(`battery.generate_skill`) + 1-2 sandbox runs. A live bounded re-check of
the 3 skills that failed validation on the FIRST attempt (before the retry
loop existed) took 120s (ready on attempt 1), 288s (ready on attempt 2,
after a fresh LLM retry with the failure fed back), and 237s (all 3
attempts exhausted, ended `invalid` — a genuine 0.90-vs-0.99 self-
consistency near-miss, correctly NOT marked ready). So budget roughly
**1-5 minutes per skill**, not a few seconds — at the defaults (`--top 20
--limit-skills 3`, worst case 60 skill-items on a fresh DB) that's
potentially **1-5 hours** for a full cold run; consider a smaller
`--limit-skills` or splitting `--only` into batches for the first
population of a fresh box. A role with zero executable skills is reported
and skipped (no fabricated item is ever inserted) and never aborts the run.

The retry loop (`app/build_battery.py::_generate_and_validate`) tries a
FREE deterministic repair before ever burning a second LLM call: if the
grader's own last printed line is a bare number (`GRADE:0.5`) instead of
the required `GRADE:{"score": 0.5}` JSON — the #1 failure mode diagnosed
live — it's wrapped and re-validated without another `generate_skill` call
(see `_repair_bare_grade_grader`). Only a genuine self-consistency miss
(reference_code doesn't satisfy its own grader) triggers a fresh LLM
attempt, with the concrete failure reason fed back as a corrective turn.
After 3 exhausted attempts the LAST attempt's content is still persisted as
`status='invalid'` with the reason logged — kept for audit, never silently
dropped, and never aborts the role.

```bash
# 2) run verify for each of the 5 gallery agents, so their receipts show up
#    on GET /api/gallery[/{slug}] — run this AFTER step 1 has given each
#    gallery role at least one 'ready' battery item, else exec_skills stays 0
sudo docker exec team247-prod python -c "
from app import db
from app.services import verify_service

rows = db.query('SELECT slug, team_id, agent_id FROM gallery_agents')
for r in rows:
    try:
        out = verify_service.verify(r['team_id'], r['agent_id'])
        print(f\"[{r['slug']}] coverage_pct={out['coverage_pct']} \"
              f\"exec_skills={len(out['exec_results'])}\")
    except Exception as e:
        print(f\"[{r['slug']}] FAILED: {e}\")
"
```

Cost/time: one `verify()` call per gallery agent — the executed track runs
every 'ready' battery item for that agent's role (a few seconds each, same
sandbox as above) plus the rubric track's `RUBRIC_CAP` (6) LLM-judged
skills (`battery.rubric_score`, one call each). Expect roughly the same
per-agent latency as a live user's "Try this agent" -> verify would see;
5 agents total, so a few minutes end-to-end, not hours.

There's no scheduled/automatic run for either step — re-run step 1 after a
deploy that changes the framework dataset or the grader-generation prompt
(`battery.generate_skill`), and re-run step 2 after any re-population that
touched a gallery role's battery items (a stale verify run is otherwise
silently left in place — `verify_runs` is one row per team+agent, replaced
in full on each call). Verify with:

```bash
curl -s https://team247.io/api/gallery | python -m json.tool
# -> each item now optionally carries "proven": true/false and "exec_skills": N
#    (absent entirely for any agent that hasn't been through both steps yet —
#    never a fabricated placeholder)
```
