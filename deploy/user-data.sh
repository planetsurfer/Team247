#!/bin/bash
# Iteration 3 EC2 bootstrap (AL2023, arm64 / t4g.small). Consumed by infra.sh as
# EC2 user-data — runs once as root on first boot. Idempotent-ish: re-running
# (e.g. by re-launching a replacement instance from the same user-data) is safe
# since it re-clones into a clean /opt/team247 each time.
#
# Everything is logged to /var/log/team247-bootstrap.log for the orchestrator to
# poll via SSM send-command.
set -euo pipefail
exec > >(tee -a /var/log/team247-bootstrap.log) 2>&1

echo "=== team247 bootstrap started at $(date -u -Iseconds) ==="

export AWS_DEFAULT_REGION=ap-southeast-1
export DEBIAN_FRONTEND=noninteractive

echo "--- installing docker + git ---"
dnf -y install docker git
systemctl enable --now docker

echo "--- installing docker compose v2 plugin ---"
mkdir -p /usr/local/lib/docker/cli-plugins
if dnf list docker-compose-plugin >/dev/null 2>&1; then
  dnf -y install docker-compose-plugin
else
  COMPOSE_VERSION="v2.29.7"
  curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-aarch64" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi
docker compose version

echo "--- ensuring aws cli is present ---"
if ! command -v aws >/dev/null 2>&1; then
  dnf -y install unzip
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install
  rm -rf /tmp/awscliv2.zip /tmp/aws
fi
aws --version

echo "--- cloning repo ---"
rm -rf /opt/team247
git clone --branch feat/per-agent-specs-ka-guidance https://github.com/planetsurfer/Team247.git /opt/team247
cd /opt/team247

echo "--- preparing data dir ---"
mkdir -p data
chown -R 10001:10001 data

echo "--- fetching secrets from SSM ---"
bash deploy/fetch_secrets.sh

echo "--- starting app (prod + aws overlay) ---"
docker compose -f docker-compose.prod.yml -f deploy/docker-compose.aws.yml up -d --build

echo "--- starting caddy ---"
docker compose -f deploy/docker-compose.caddy.yml up -d

echo "--- containers ---"
docker ps

echo "=== team247 bootstrap finished at $(date -u -Iseconds) ==="
