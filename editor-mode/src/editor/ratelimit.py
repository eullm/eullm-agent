"""Wrong passwords counted per address (audit 2026-11-10, B1 of the audit of
2026-10-27). The lock of an account after a few wrong passwords let anyone
who knew an email keep its owner out: now the few tries are counted for the
address they come from and that email, so whoever tries is stopped and the
owner, from their own address, still gets in. An address that tries many
emails is stopped as a whole. The account itself locks only after many
wrong passwords from many addresses (accounts.MAX_FAILED_LOGINS).

Kept in memory: the dashboard is one process (editor serve), and a restart
only gives a few more tries."""

from __future__ import annotations

import threading
import time
from collections import deque

WINDOW_SECONDS = 15 * 60
PER_EMAIL = 5  # wrong passwords for one email from one address
PER_ADDRESS = 20  # wrong passwords from one address, whatever the email
_MAX_KEYS = 50_000  # beyond this the oldest records go: memory stays bounded


class Limiter:
    def __init__(self, window: float = WINDOW_SECONDS, per_email: int = PER_EMAIL, per_address: int = PER_ADDRESS,
                 clock=time.monotonic):
        self.window, self.per_email, self.per_address, self.clock = window, per_email, per_address, clock
        self._hits: dict[tuple, deque] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple, now: float) -> deque:
        q = self._hits.get(key)
        if q is None:
            return deque()
        while q and q[0] <= now - self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
        return q

    def blocked(self, address: str | None, email: str = "") -> bool:
        """Whether this address may not try (this email) now."""
        now = self.clock()
        with self._lock:
            if len(self._recent(("a", address), now)) >= self.per_address:
                return True
            return bool(email) and len(self._recent(("e", address, email.strip().lower()), now)) >= self.per_email

    def failed(self, address: str | None, email: str = "") -> None:
        now = self.clock()
        with self._lock:
            if len(self._hits) > _MAX_KEYS:
                for key in list(self._hits)[: len(self._hits) // 2]:
                    self._hits.pop(key, None)
            keys = [("a", address)] + ([("e", address, email.strip().lower())] if email else [])
            for key in keys:
                self._hits.setdefault(key, deque()).append(now)

    def succeeded(self, address: str | None, email: str) -> None:
        with self._lock:
            self._hits.pop(("e", address, email.strip().lower()), None)
