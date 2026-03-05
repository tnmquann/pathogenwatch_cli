"""
auth.py – Provides a configured requests.Session using the PathogenWatch
X-API-Key authentication scheme.

Spec reference (openapi.json):
  securitySchemes.APIKeyHeader:
    type: apiKey
    in: header
    name: X-API-Key
"""

import requests


class AuthSession:
    """Thin wrapper around requests.Session that injects the X-API-Key header."""

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-Key": api_key,
                "Accept": "application/json",
            }
        )

    def verify(self) -> bool:
        """
        Call GET /api/user/access to confirm the API key is valid.

        HTTP status meanings:
          200  -> key is valid
          401  -> key is missing or malformed
          403  -> key is recognised but not authorised
          404  -> endpoint not found on this server (non-fatal, skip silently)
        Returns True when the server explicitly confirms the key is valid,
        or when the endpoint is absent (404) — in that case we let the
        actual API calls surface any auth error naturally.
        """
        try:
            resp = self.session.get(
                f"{self.base_url}/api/user/access",
                timeout=10,
            )
            if resp.status_code == 200:
                print("[auth] API key verified successfully.")
                return True
            if resp.status_code == 404:
                # Endpoint does not exist on this server instance.
                # Not an auth error — skip silently.
                return True
            if resp.status_code in (401, 403):
                print(
                    f"[auth] ERROR: API key rejected (HTTP {resp.status_code}). "
                    "Check that the key is correct and has not expired."
                )
                return False
            # Unexpected status — warn but do not block.
            print(
                f"[auth] WARNING: /api/user/access returned HTTP {resp.status_code}. "
                "Continuing anyway."
            )
            return False
        except requests.RequestException as exc:
            print(f"[auth] WARNING: Could not reach {self.base_url}: {exc}")
            return False
