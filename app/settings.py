"""App settings — APP_* env vars. Deliberately NOT named config.py, to avoid
shadowing the root config.py (which holds the Alibaba LLM client + LocalRunner).
Import this as `from app import settings`."""
import os

APP_DB_PATH = os.getenv("APP_DB_PATH", "data/agentproof.db")
APP_ADMIN_TOKEN = os.getenv("APP_ADMIN_TOKEN", "")   # required bearer for /verify, /distill, --distill-all
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "10"))   # per-IP, LLM-driving endpoints
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")        # localhost-bound by default (LocalRunner runs as host user)
APP_PORT = int(os.getenv("APP_PORT", "8000"))
APP_BASE_URL = os.getenv("APP_BASE_URL", "/")
SPEC_TTL_DAYS = int(os.getenv("SPEC_TTL_DAYS", "7"))  # team_spec_versions freshness before forced re-render
PRE_DISTILL_POPULAR = int(os.getenv("PRE_DISTILL_POPULAR", "50"))  # top-N roles to pre-distill at startup

# Closed-beta token auth (PRODUCTION_ROADMAP.md P0 #1). Fail-secure default: beta
# auth is ON unless explicitly turned off (BETA_AUTH=off), so a missing env var
# in a deployed environment never accidentally opens the gate.
BETA_AUTH = os.getenv("BETA_AUTH", "on") != "off"
BETA_TOKEN_SALT = os.getenv("BETA_TOKEN_SALT", "")  # mixed into every token hash


def admin_token_ok(header_value: str) -> bool:
    """True if the Authorization header carries the configured admin token."""
    if not APP_ADMIN_TOKEN:
        return False
    if not header_value:
        return False
    return header_value.lower().startswith("bearer ") and header_value[7:].strip() == APP_ADMIN_TOKEN
