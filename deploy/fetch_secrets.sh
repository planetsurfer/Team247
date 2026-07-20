#!/usr/bin/env bash
# Fetches closed-beta secrets from SSM Parameter Store into deploy/secrets.env.
#
# Run ON the EC2 box (aws cli present, instance role/profile with ssm:GetParameter
# on /team247/prod/*). docker-compose.prod.yml's `env_file: deploy/secrets.env`
# reads the result. deploy/secrets.env is gitignored — NEVER commit it. Re-run
# after rotating any secret (PRODUCTION_ROADMAP.md P0 #2).
#
# Params read (SSM Parameter Store, --with-decryption for SecureString):
#   /team247/prod/LLM_API_KEY      SecureString, required
#   /team247/prod/APP_ADMIN_TOKEN  SecureString, required
#   /team247/prod/BETA_TOKEN_SALT  SecureString, required
#   /team247/prod/LLM_MODEL        String, optional (default: kimi-k2.6)
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_FILE="$DEPLOY_DIR/secrets.env"
PREFIX="/team247/prod"

# name -> default. Empty default means the param is required; a missing/empty
# fetch for it is a fatal error.
declare -A DEFAULTS=(
  [LLM_API_KEY]=""
  [APP_ADMIN_TOKEN]=""
  [BETA_TOKEN_SALT]=""
  [LLM_MODEL]="kimi-k2.6"
)
ORDER=(LLM_API_KEY APP_ADMIN_TOKEN BETA_TOKEN_SALT LLM_MODEL)

fetch_param() {
  aws ssm get-parameter \
    --name "${PREFIX}/${1}" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text 2>/dev/null
}

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

for name in "${ORDER[@]}"; do
  default="${DEFAULTS[$name]}"
  value=""
  if fetched="$(fetch_param "$name")" && [ -n "$fetched" ] && [ "$fetched" != "None" ]; then
    value="$fetched"
  elif [ -n "$default" ]; then
    value="$default"
    echo "warn: ${PREFIX}/${name} not set in SSM — using default '${default}'" >&2
  else
    echo "error: required SSM parameter ${PREFIX}/${name} is missing (or empty) — aborting" >&2
    exit 1
  fi
  echo "${name}=${value}" >> "$tmp_file"
done

mv "$tmp_file" "$OUT_FILE"
chmod 600 "$OUT_FILE"
echo "wrote $OUT_FILE (chmod 600)"
