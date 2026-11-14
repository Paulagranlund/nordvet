"""
Small client for the VetOne API.

Handles the things the API documentation doesn't tell you, so the rest of
the pipeline doesn't have to:
  - access tokens expire (after ~1 hour)      -> refresh automatically
  - rate limiting (HTTP 429 + Retry-After)     -> wait and retry
  - random server errors (HTTP 5xx)            -> retry with backoff
  - cold start (first call can take ~20s)      -> generous timeout
  - page size silently capped at 500           -> follow next_cursor
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)

MAX_RETRIES = 8
TIMEOUT_SECONDS = 60          # covers the ~20s cold start
TOKEN_LIFETIME_SECONDS = 50 * 60  # tokens last ~60 min; refresh a bit early
PAGE_SIZE = 500               # API maximum
MIN_SECONDS_BETWEEN_REQUESTS = 0.5  # be polite to the client's production system


class VetOneClient:
    def __init__(self, base_url=None, client_id=None, client_secret=None):
        load_dotenv()  # reads .env into environment variables
        self.base_url = (base_url or os.environ["VETONE_BASE_URL"]).rstrip("/")
        self.client_id = client_id or os.environ["VETONE_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["VETONE_CLIENT_SECRET"]
        self.session = requests.Session()  # reuses the connection between calls
        self._token = None
        self._token_fetched_at = 0.0
        self._last_request_at = 0.0

    # ---------- authentication ----------

    def _get_token(self):
        """Return a valid token, fetching a new one if missing or about to expire."""
        too_old = time.time() - self._token_fetched_at > TOKEN_LIFETIME_SECONDS
        if self._token is None or too_old:
            log.info("Fetching new access token")
            resp = self.session.post(
                f"{self.base_url}/auth/token",
                json={"client_id": self.client_id, "client_secret": self.client_secret},
                timeout=TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
            self._token_fetched_at = time.time()
        return self._token

    # ---------- one request, with retries ----------

    def _get(self, path, params=None):
        """GET one URL. Retries on rate limits, server errors and network hiccups."""
        for attempt in range(1, MAX_RETRIES + 1):
            headers = {"Authorization": f"Bearer {self._get_token()}"}
            # proactive throttling: short pause so we rarely hit the rate limit
            pause = MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - self._last_request_at)
            if pause > 0:
                time.sleep(pause)
            self._last_request_at = time.time()
            try:
                resp = self.session.get(
                    f"{self.base_url}{path}",
                    params=params,  # requests URL-encodes these (e.g. '+' in timestamps)
                    headers=headers,
                    timeout=TIMEOUT_SECONDS,
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                wait = 2 ** attempt
                log.warning("Network error (%s), retry %d in %ds", e, attempt, wait)
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 401:
                # token expired earlier than expected: force a new one and retry
                log.warning("401 Unauthorized, refreshing token")
                self._token = None
                continue

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                log.warning("Rate limited, waiting %ds (attempt %d)", wait, attempt)
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                wait = 2 ** attempt  # exponential backoff: 2, 4, 8, ... seconds
                log.warning("Server error %d, retry %d in %ds", resp.status_code, attempt, wait)
                time.sleep(wait)
                continue

            # any other error (e.g. 400 bad parameter) is our mistake: fail loudly
            raise RuntimeError(f"GET {path} failed: {resp.status_code} {resp.text}")

        raise RuntimeError(f"GET {path} failed after {MAX_RETRIES} attempts")

    # ---------- endpoints ----------

    def get_clinics(self):
        return self._get("/v1/clinics")["data"]

    def iter_appointments(self, **filters):
        """
        Yield pages (lists) of appointments, following next_cursor until done.

        Example:
            client.iter_appointments(updated_since="2026-11-01T00:00:00+00:00",
                                     include_deleted="true")
        """
        params = {k: v for k, v in filters.items() if v is not None}
        params["limit"] = PAGE_SIZE
        while True:
            body = self._get("/v1/appointments", params)
            yield body["data"]
            cursor = body.get("next_cursor")
            if not cursor:
                break
            params["cursor"] = cursor
