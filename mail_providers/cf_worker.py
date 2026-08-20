"""Cloudflare Email Worker catch-all mail provider.

Reads verification codes from a self-hosted Cloudflare Worker that exposes a
public HTTP API in front of a catch-all mailbox (Cloudflare Email Routing ->
Worker -> D1 store). This lets registration run without IMAP credentials.

Endpoints (relative to the configured API base, e.g. https://mail.example.com):
    GET /api/code?email=xxx@example.com
        -> {"email": "...", "code": "123456", "timestamp": ..., "status": "waiting"}
    GET /api/inbox?email=xxx@example.com
        -> {"email": "...", "count": N, "mails": [...]}

Environment variables:
    CF_EMAIL_API_BASE   required, e.g. https://mail.example.com
    CF_EMAIL_DOMAIN     required, the catch-all domain, e.g. example.com
    CF_EMAIL_PREFIX     optional, local-part prefix (default "kiro")
"""
from __future__ import annotations

import os
import re
import secrets
import time

from .base import MailProvider


class CfEmailWorkerProvider(MailProvider):
    """Catch-all mailbox served by a Cloudflare Email Worker HTTP API."""

    name = "cf_worker"
    display_name = "Cloudflare Email Worker"

    def __init__(
        self,
        api_base: str = "",
        domain: str = "",
        prefix: str = "",
        timeout: int = 120,
    ):
        self.api_base = (api_base or os.environ.get("CF_EMAIL_API_BASE", "")).rstrip("/")
        self.domain = (domain or os.environ.get("CF_EMAIL_DOMAIN", "")).strip().lower()
        self.prefix = prefix or os.environ.get("CF_EMAIL_PREFIX", "kiro")
        self.timeout = int(timeout or 120)

        if not self.api_base:
            raise ValueError("CF_EMAIL_API_BASE is required for the cf_worker provider")
        if not self.domain:
            raise ValueError("CF_EMAIL_DOMAIN is required for the cf_worker provider")

        self.address: str | None = None

    # ------------------------------------------------------------------
    # MailProvider interface
    # ------------------------------------------------------------------

    def create_mailbox(self) -> str:
        local = f"{self.prefix}.{secrets.token_hex(4)}"
        self.address = f"{local}@{self.domain}"
        return self.address

    def wait_otp(self, timeout: int = 120, poll_interval: int = 3) -> str:
        if not self.address:
            raise RuntimeError("Call create_mailbox() before wait_otp().")
        from curl_cffi import requests as curl_requests

        deadline = time.time() + max(int(timeout or self.timeout), 1)
        while time.time() < deadline:
            try:
                resp = curl_requests.get(
                    f"{self.api_base}/api/code",
                    params={"email": self.address},
                    timeout=10,
                    verify=False,
                )
                data = resp.json() if resp.status_code == 200 else {}
                code = data.get("code")
                if code:
                    return str(code).strip()
            except Exception:
                pass  # transient network error -> keep polling
            time.sleep(max(1, int(poll_interval)))
        return ""

    def list_domains(self) -> list[dict]:
        return [{"id": self.domain, "domain": self.domain}]