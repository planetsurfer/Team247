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
