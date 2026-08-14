"""Self-hosted Google Workspace / IMAP catch-all mail provider.

Designed for users who own a pool of domains whose MX records forward every
alias (`*@domain.tld`) to a single Gmail/Workspace inbox. Creating a mailbox
simply invents a fresh `<random>@<random-domain>` alias — the inbound server
accepts anything, the catch-all routing forwards it here, and we poll the IMAP
account for the OTP message addressed to that alias.

Works out-of-the-box with:
- Google Workspace "Default routing" rule set to forward unknown addresses to a
  master inbox (Admin Console -> Apps -> Gmail -> Default routing).
- Cloudflare Email Routing catch-all forwarding to a Gmail account.
- Any other catch-all setup where a regular IMAP account receives everything.

Requires:
- An app password (Gmail) or a regular IMAP password.
- The pool of domains listed in a plain-text file (one domain per line) OR
  passed in as a list to the constructor.
"""
from __future__ import annotations

import email
import email.message
import email.utils
import imaplib
import os
import random
import re
import string
import time
from email.header import decode_header
from pathlib import Path

from .base import MailProvider


_DEFAULT_DOMAINS_FILE = Path(__file__).resolve().parent.parent / "domains.txt"


def _load_domains_from_file(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [line.strip().lower() for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _random_local(length: int = 10) -> str:
    """DEPRECATED: use _generate_human_email instead."""
    pool = string.ascii_lowercase + string.digits
    return "".join(random.choices(pool, k=length))


def _generate_human_email(max_length: int = 20, custom_first: list[str] = None, custom_last: list[str] = None) -> str:
    """
    Generate human-like email local part (a-z0-9.-).

    Patterns:
    - firstname.lastname
    - firstname.lastname123
    - firstname
    - lastname
    - name-name or name.name
    - name123

    Args:
        max_length: Max chars for local part (default 20)
        custom_first: Additional first names from env
        custom_last: Additional last names from env

    Returns local part only (before @), max 20 chars.
    """
    pool_digit = string.digits

    # Default name banks
    first = ["john", "jane", "alex", "chris", "sam", "taylor", "jordan", "casey",
             "morgan", "riley", "drew", "avery", "charlie", "jamie", "robin"]
    last = ["smith", "jones", "brown", "wilson", "davis", "miller", "moore", "taylor",
            "anderson", "thomas", "jackson", "white", "harris", "martin", "thompson"]

    # Merge custom names from env
    if custom_first:
        first = first + [n.lower().strip() for n in custom_first if n.strip()]
    if custom_last:
        last = last + [n.lower().strip() for n in custom_last if n.strip()]

    pattern = random.randint(1, 6)

    if pattern == 1:
        # firstname.lastname
        f = random.choice(first)
        l = random.choice(last)
        local = f"{f}.{l}"
    elif pattern == 2:
        # firstname.lastname{2-3 digits}
        f = random.choice(first)
        l = random.choice(last)
        num = "".join(random.choices(pool_digit, k=random.randint(2, 3)))
        local = f"{f}.{l}{num}"
    elif pattern == 3:
        # name-name or name.name
        f = random.choice(first)
        l = random.choice(last)
        sep = random.choice(["-", "."])
        local = f"{f}{sep}{l}"
    elif pattern == 4:
        # name{3-5 digits}
        name = random.choice(first + last)
        num = "".join(random.choices(pool_digit, k=random.randint(3, 5)))
        local = f"{name}{num}"
    elif pattern == 5:
        # firstname only
        local = random.choice(first)
    else:
        # lastname only
        local = random.choice(last)

    # Truncate to max_length
    if len(local) > max_length:
        local = local[:max_length]

    return local


def _decode_header_value(raw: str) -> str:
    """Best-effort decode of a MIME-encoded email header."""
    if not raw:
        return ""
    parts = []
    for chunk, enc in decode_header(raw):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="ignore"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="ignore"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _extract_bodies(msg: email.message.Message) -> list[str]:
    """Return every text/* part body as a list of decoded strings."""
    out: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype.startswith("text/"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        out.append(payload.decode(charset, errors="ignore"))
                    except LookupError:
                        out.append(payload.decode("utf-8", errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                out.append(payload.decode(charset, errors="ignore"))
            except LookupError:
                out.append(payload.decode("utf-8", errors="ignore"))
        elif msg.get_payload():
            out.append(str(msg.get_payload()))
    return out


class GsuiteImapProvider(MailProvider):
    """Catch-all IMAP provider (Google Workspace, Cloudflare Email Routing, etc.)."""

    name = "gsuite_imap"
    display_name = "Gsuite/IMAP (self-hosted)"

    def __init__(
        self,
        imap_server: str = "imap.gmail.com",
        imap_port: int = 993,
        imap_user: str = "",
        imap_pass: str = "",
        imap_folder: str = "INBOX",
        domains: list[str] | None = None,
        domains_file: str | Path | None = None,
        local_prefix: str = "",
        local_length: int = 10,
        email_filter: str = "",
        domain_pool=None,
    ):
        self.imap_server = imap_server
        self.imap_port = int(imap_port) if imap_port else 993
        self.imap_user = imap_user
        self.imap_pass = imap_pass
        self.imap_folder = imap_folder or "INBOX"
        self.local_prefix = local_prefix or ""
        self.local_length = max(4, int(local_length))

        # Email filter from env/config or default AWS addresses
        self.email_filter = email_filter or os.environ.get(
            "EMAIL_AWS",
            "no-reply@amazonaws.com,no-reply@signin.aws"
        )

        # Domain pool manager (optional)
        self.domain_pool = domain_pool

        # Custom name banks from env (comma-separated)
        custom_first_env = os.environ.get("EMAIL_FIRST_NAMES", "")
        custom_last_env = os.environ.get("EMAIL_LAST_NAMES", "")
        self.custom_first_names = [n.strip() for n in custom_first_env.split(",") if n.strip()]
        self.custom_last_names = [n.strip() for n in custom_last_env.split(",") if n.strip()]

        # Resolve the domain pool.
        pool: list[str] = []
        if domains:
            pool = [d.strip().lower() for d in domains if d and d.strip()]
        elif domains_file:
            pool = _load_domains_from_file(domains_file)
        else:
            pool = _load_domains_from_file(_DEFAULT_DOMAINS_FILE)
        if not pool:
            raise ValueError(
                "GsuiteImapProvider: empty domain pool. Pass domains=[...] or point "
                "domains_file to a non-empty file with one domain per line."
            )
        self.domains: list[str] = pool

        # Per-mailbox state populated by create_mailbox().
        self.address: str | None = None
        self._created_at: float = 0.0
        # Monotonically-bumped set of UIDs we've already consumed so the same
        # OTP isn't returned twice if the caller reuses the provider.
        self._seen_uids: set[str] = set()
        # Collision tracking: {domain: set(local_parts)} to prevent duplicates
        self._used_locals: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # MailProvider interface
    # ------------------------------------------------------------------

    def create_mailbox(self) -> str:
        # Use domain pool if available, fallback to random choice
        if self.domain_pool:
            domain = self.domain_pool.get_domain()
            if not domain:
                raise RuntimeError("All domains suspended due to TES blocks. Waiting for recovery...")
        else:
            domain = random.choice(self.domains)

        # Initialize tracking for this domain
        if domain not in self._used_locals:
            self._used_locals[domain] = set()

        # Generate unique local part (max 10 attempts to avoid collision)
        for _ in range(10):
            local = _generate_human_email(20, self.custom_first_names, self.custom_last_names)
            if local not in self._used_locals[domain]:
                self._used_locals[domain].add(local)
                break
        else:
            # Fallback: append timestamp if all attempts collide
            local = f"{local}{int(time.time()) % 10000}"

        self.address = f"{local}@{domain}"
        self._created_at = time.time()
        self._seen_uids = set()
        return self.address

    def wait_otp(self, timeout: int = 120, poll_interval: int = 1) -> str:
        import sys

        if not self.address:
            raise RuntimeError("Call create_mailbox() before wait_otp().")
        if not self.imap_user or not self.imap_pass:
            raise RuntimeError("IMAP credentials missing (imap_user / imap_pass).")

        print(f"[IMAP DEBUG] ========== Starting OTP polling ==========", flush=True)
        print(f"[IMAP DEBUG] Target email: {self.address}", flush=True)
        print(f"[IMAP DEBUG] Timeout: {timeout}s, Poll interval: {poll_interval}s", flush=True)
        print(f"[IMAP DEBUG] Initial _seen_uids: {self._seen_uids}", flush=True)
        print(f"[IMAP DEBUG] _created_at timestamp: {self._created_at}", flush=True)

        deadline = time.time() + max(timeout, 1)
        target = self.address.lower()

        attempt = 0
        while time.time() < deadline:
            attempt += 1
            remaining = deadline - time.time()
            print(f"[IMAP DEBUG] ========== Poll attempt #{attempt} ==========", flush=True)
            print(f"[IMAP DEBUG] Remaining time: {remaining:.1f}s", flush=True)
            try:
                code = self._poll_once(target)
                if code:
                    print(f"[IMAP DEBUG] ========== OTP FOUND: {code} ==========", flush=True)
                    return code
            except imaplib.IMAP4.error as e:
                # Transient IMAP error — reconnect on the next loop.
                print(f"[IMAP DEBUG] IMAP4.error caught: {e}", flush=True)
                pass
            except Exception as e:
                # Don't let exotic errors kill the polling loop.
                print(f"[IMAP DEBUG] Exception caught during poll: {type(e).__name__}: {e}", flush=True)
                import traceback
                print(f"[IMAP DEBUG] Traceback: {traceback.format_exc()}", flush=True)
                pass
            time.sleep(max(1, int(poll_interval)))

        print(f"[IMAP DEBUG] ========== TIMEOUT ==========", flush=True)
        print(f"[IMAP DEBUG] Total poll attempts: {attempt}", flush=True)
        print(f"[IMAP DEBUG] Final _seen_uids: {self._seen_uids}", flush=True)
        return ""

    def list_domains(self) -> list[dict]:
        return [{"id": d, "domain": d} for d in self.domains]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        imap = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
        imap.login(self.imap_user, self.imap_pass)
        imap.select(self.imap_folder, readonly=False)
        return imap

    def _poll_once(self, target_address: str) -> str:
        """Single IMAP poll iteration. Returns the 6-digit OTP or empty string."""
        print(f"[IMAP DEBUG] _poll_once called for: {target_address}", flush=True)
        print(f"[IMAP DEBUG] Connecting to IMAP server: {self.imap_server}:{self.imap_port}", flush=True)
        imap = self._connect()
        print(f"[IMAP DEBUG] IMAP connection successful, folder selected: {self.imap_folder}", flush=True)
        try:
            # Narrow to messages delivered after create_mailbox().
            # IMAP SINCE granularity is day-level, so we always fetch at least
            # the current day and then filter in-memory by the actual timestamp.
            # Changed: Using 10 minutes window instead of 24 hours for faster, focused search
            since_date = time.strftime(
                "%d-%b-%Y", time.gmtime(max(self._created_at - 600, 0))
            )

            # Search for TO + SINCE (10 minutes window)
            # Search by SINCE + FROM only (TO filter unreliable with catch-all forwarding)
            # Build FROM clauses from email_filter (comma-separated)
            from_addresses = [addr.strip() for addr in self.email_filter.split(",") if addr.strip()]
            from_clauses = " ".join(f'FROM "{addr}"' for addr in from_addresses)
            search_query = f'(SINCE "{since_date}" OR {from_clauses})'
            print(f"[IMAP DEBUG] ========== Search Phase ==========", flush=True)
            print(f"[IMAP DEBUG] SINCE date: {since_date}", flush=True)
            print(f"[IMAP DEBUG] Search query: {search_query}", flush=True)
            print(f"[IMAP DEBUG] Will filter by TO header after fetch: {target_address}", flush=True)
            status, data = imap.uid(
                "SEARCH", None, search_query
            )
            print(f"[IMAP DEBUG] Search status: {status}", flush=True)
            uids: list[str] = []
            if status == "OK" and data and data[0]:
                uids = data[0].decode(errors="ignore").split()
                print(f"[IMAP DEBUG] Total UIDs found: {len(uids)}", flush=True)
                print(f"[IMAP DEBUG] All UIDs: {uids}", flush=True)
                print(f"[IMAP DEBUG] Current _seen_uids: {self._seen_uids}", flush=True)
            else:
                print(f"[IMAP DEBUG] Search returned no results (status={status}, data={data})", flush=True)

            # Fallback: search by subject if FROM search fails
            if not uids:
                print(f"[IMAP DEBUG] Trying fallback search by SUBJECT...", flush=True)
                subject_query = f'(SINCE "{since_date}" SUBJECT "Verify your AWS Builder ID email address")'
                print(f"[IMAP DEBUG] Fallback query: {subject_query}", flush=True)
                status, data = imap.uid("SEARCH", None, subject_query)
                if status == "OK" and data and data[0]:
                    uids = data[0].decode(errors="ignore").split()
                    print(f"[IMAP DEBUG] Fallback search found {len(uids)} messages", flush=True)

            # Scan newest first so we get the most recent OTP.
            unseen_uids = [uid for uid in reversed(uids) if uid not in self._seen_uids]
            print(f"[IMAP DEBUG] Unseen UIDs after filtering: {unseen_uids}", flush=True)
            print(f"[IMAP DEBUG] ========== Processing Messages ==========", flush=True)

            for uid in unseen_uids:
                print(f"[IMAP DEBUG] --- Fetching UID: {uid} ---", flush=True)
                self._seen_uids.add(uid)
                status, msg_data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)

                # DEBUG: Log message headers
                from_addr = _decode_header_value(msg.get("From", ""))
                subject = _decode_header_value(msg.get("Subject", ""))
                print(f"[IMAP DEBUG] Message UID:{uid} From:{from_addr[:50]} Subject:{subject[:50]}", flush=True)

                # Sanity: the `To:` (or `Delivered-To:` / `X-Original-To:`)
                # header should mention the target alias. Catch-all setups
                # keep the alias in the `To:` header, Cloudflare Routing
                # sometimes strips it into `X-Forwarded-To:` instead.
                to_haystack = " ".join(
                    _decode_header_value(msg.get(h, ""))
                    for h in ("To", "Delivered-To", "X-Original-To",
                              "X-Forwarded-To", "X-Delivered-To")
                ).lower()

                print(f"[IMAP DEBUG]   To headers: {to_haystack[:100]}", flush=True)

                if target_address not in to_haystack:
                    print(f"[IMAP DEBUG]   ❌ SKIPPED: Target address not in To headers", flush=True)
                    continue

                # Timestamp filter: ignore pre-mailbox-creation messages.
                ts = self._message_epoch(msg)
                if ts and ts + 5 < self._created_at:
                    print(f"[IMAP DEBUG]   ❌ SKIPPED: Message too old (ts={ts}, created={self._created_at})", flush=True)
                    continue

                # Prefer the subject line (many OTP emails put the code in the
                # subject, e.g. "Your verification code is 123456").
                m = re.search(r"\b(\d{6})\b", subject)
                if m:
                    otp_code = m.group(1)
                    print(f"[IMAP DEBUG]   FOUND OTP in subject: {otp_code}", flush=True)
                    # Delete email after extracting OTP (like grok flow)
                    try:
                        imap.uid("STORE", uid, "+FLAGS", "\\Deleted")
                        imap.expunge()
                        print(f"[IMAP DEBUG]   Deleted OTP email UID:{uid}", flush=True)
                    except Exception as e:
                        print(f"[IMAP DEBUG]   Failed to delete email: {e}", flush=True)
                    return otp_code

                for body in _extract_bodies(msg):
                    m = re.search(r"\b(\d{6})\b", body)
                    if m:
                        otp_code = m.group(1)
                        print(f"[IMAP DEBUG]   FOUND OTP in body: {otp_code}", flush=True)
                        # Delete email after extracting OTP (like grok flow)
                        try:
                            imap.uid("STORE", uid, "+FLAGS", "\\Deleted")
                            imap.expunge()
                            print(f"[IMAP DEBUG]   Deleted OTP email UID:{uid}", flush=True)
                        except Exception as e:
                            print(f"[IMAP DEBUG]   Failed to delete email: {e}", flush=True)
                        return otp_code

                print(f"[IMAP DEBUG]   No 6-digit code found in this message", flush=True)

            print(f"[IMAP DEBUG] Poll iteration complete - no OTP found in {len(unseen_uids)} unseen messages", flush=True)
            return ""
        finally:
            try:
                imap.close()
            except Exception:
                pass
            try:
                imap.logout()
            except Exception:
                pass
        return ""

    @staticmethod
    def _message_epoch(msg: email.message.Message) -> float:
        date_hdr = msg.get("Date") or msg.get("Received", "", flush=True)
        try:
            tup = email.utils.parsedate_tz(date_hdr)
            if tup:
                return email.utils.mktime_tz(tup)
        except Exception:
            pass
        return 0.0
