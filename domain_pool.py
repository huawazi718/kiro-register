"""
Domain pool manager with TES block suspension and auto-recovery.

Tracks domain health and suspends domains that trigger AWS TES blocks.
Automatically unsuspends domains after a configurable duration.
"""
import time
from pathlib import Path
from typing import Optional


class DomainPool:
    """Manages domain rotation with suspension for TES-blocked domains."""

    def __init__(self, domains: list[str], suspend_duration_seconds: int = 3600):
        """
        Initialize domain pool.

        Args:
            domains: List of available domains
            suspend_duration_seconds: How long to suspend blocked domains (default 1h)
        """
        self.domains = list(domains)
        self.suspend_duration = suspend_duration_seconds
        # {domain: timestamp_when_suspended}
        self.suspended: dict[str, float] = {}
        # Sticky domain: reuse last successful domain until it gets suspended
        self.current_domain: str | None = None
        self.current_index = 0

    def get_domain(self) -> Optional[str]:
        """
        Get sticky domain (reuse until suspended).

        Auto-unsuspends domains past their suspension duration.
        Sticks to current_domain until it gets suspended, then picks new one.

        Returns:
            Domain string, or None if all suspended
        """
        # Unsuspend expired domains
        now = time.time()
        expired = [d for d, ts in self.suspended.items() if now >= ts + self.suspend_duration]
        for d in expired:
            del self.suspended[d]

        # Find available domains
        available = [d for d in self.domains if d not in self.suspended]
        if not available:
            return None

        # If current domain still available, keep using it (sticky)
        if self.current_domain and self.current_domain in available:
            return self.current_domain

        # Pick new domain (current was suspended or first call)
        import random
        self.current_domain = random.choice(available)
        return self.current_domain

    def suspend_domain(self, domain: str):
        """Mark domain as suspended due to TES block."""
        if domain in self.domains:
            self.suspended[domain] = time.time()

    def get_stats(self) -> dict:
        """Return pool statistics."""
        now = time.time()
        available = [d for d in self.domains if d not in self.suspended]
        suspended_count = len(self.suspended)

        # Find next unsuspend time
        next_unsuspend = None
        if self.suspended:
            earliest = min(ts + self.suspend_duration for ts in self.suspended.values())
            next_unsuspend = int(earliest - now)

        return {
            "total": len(self.domains),
            "available": len(available),
            "suspended": suspended_count,
            "next_unsuspend_seconds": next_unsuspend,
        }


def load_domain_pool_from_config(cfg: dict, config_path: Path) -> Optional[DomainPool]:
    """
    Load domain pool from config if using gsuite_imap provider.

    Args:
        cfg: Configuration dict
        config_path: Path to config file (for resolving domains path)

    Returns:
        DomainPool instance or None
    """
    if cfg.get("mail_provider", "").lower() != "gsuite_imap":
        return None

    domains = cfg.get("domains", [])
    if not domains:
        return None

    suspend_duration = int(cfg.get("domain_suspend_duration", 3600))
    return DomainPool(domains, suspend_duration)
