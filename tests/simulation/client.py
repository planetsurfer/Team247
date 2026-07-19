"""HTTP client for the simulation harness: pacing + retry over requests.

Two classes of endpoint:
  - rate-limited (intake/start, intake/*/answer, intake/*/recommend,
    team/recommend) — every call passes through a shared token bucket so the
    harness self-paces at --target-rpm even when the server's
    RATE_LIMIT_PER_MIN has been raised; app-side 429s (Retry-After) are
    honoured as a safety net and counted separately from LLM-quota errors.
  - unlimited (catalog, jobs, health) — no pacing, plain retry.
"""
from __future__ import annotations

import threading
import time

import requests

# Matches the existing suite's ceiling: recommend drives a multi-step LLM
# pipeline and routinely takes 20-60s; cold-role /card distills synchronously.
TIMEOUT = 150
RETRIES_5XX = 3
RETRIES_429 = 3
BACKOFF_5XX = (5, 15, 45)


class SimClient:
    """requests.Session wrapper with a thread-safe client-side token bucket."""

    def __init__(self, base_url: str, target_rpm: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self._min_interval = 60.0 / max(target_rpm, 0.1)
        self._lock = threading.Lock()
        self._next_slot = 0.0  # monotonic time the next rate-limited call may fire
        self.stats = {"calls": 0, "rate_limited_calls": 0, "app_429s": 0, "http_retries": 0}

    # ── pacing ──────────────────────────────────────────────────────────────
    def _acquire_slot(self):
        """Block until this thread may make one rate-limited call."""
        with self._lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(self._next_slot, now) + self._min_interval
        if wait > 0:
            time.sleep(wait)

    # ── core request with retry ─────────────────────────────────────────────
    def _request(self, method: str, path: str, *, json=None, limited: bool) -> requests.Response:
        url = f"{self.base_url}{path}"
        errors_5xx = 0   # transport errors + 5xx share this budget
        errors_429 = 0   # app rate-limit retries budgeted separately
        while True:
            if limited:
                self._acquire_slot()
            self.stats["calls"] += 1
            if limited:
                self.stats["rate_limited_calls"] += 1
            try:
                r = self.session.request(method, url, json=json, timeout=TIMEOUT)
            except requests.RequestException:
                errors_5xx += 1
                if errors_5xx >= RETRIES_5XX:
                    raise
                self.stats["http_retries"] += 1
                time.sleep(BACKOFF_5XX[errors_5xx - 1])
                continue
            if r.status_code == 429:
                # App-side rate limit — distinct from provider LLM quota (which
                # surfaces as a 5xx from the app after its own retries).
                self.stats["app_429s"] += 1
                errors_429 += 1
                if errors_429 > RETRIES_429:
                    return r
                retry_after = int(r.headers.get("Retry-After", "60") or 60)
                time.sleep(min(retry_after, 120))
                continue
            if r.status_code >= 500:
                errors_5xx += 1
                if errors_5xx >= RETRIES_5XX:
                    return r  # caller sees the last 5xx response
                self.stats["http_retries"] += 1
                time.sleep(BACKOFF_5XX[errors_5xx - 1])
                continue
            return r

    def get(self, path: str) -> requests.Response:
        return self._request("GET", path, limited=False)

    def post_limited(self, path: str, json=None) -> requests.Response:
        return self._request("POST", path, json=json, limited=True)

    # ── convenience ─────────────────────────────────────────────────────────
    def health_ok(self) -> bool:
        try:
            return self.session.get(f"{self.base_url}/api/health", timeout=8).status_code < 500
        except requests.RequestException:
            return False
