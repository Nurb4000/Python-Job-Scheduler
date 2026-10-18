import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """Sliding-window failed-attempt counter.

    In-memory and per-process is fine here: this app runs a single Flask
    process (web + APScheduler thread), so there is one place where login
    attempts are counted. Keys are 'ip|username' — an attacker hammering
    one account from one IP gets locked out, while a NAT'd office is not
    locked out for everyone just because one person typo'd their password.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._failures = defaultdict(deque)

    def _prune(self, key, window, now):
        q = self._failures[key]
        cutoff = now - window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            self._failures.pop(key, None)

    def check(self, key, max_attempts, window):
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            self._prune(key, window, now)
            q = self._failures.get(key)
            if not q or len(q) < max_attempts:
                return True, 0
            retry_after = int(window - (now - q[0])) + 1
            return False, max(retry_after, 1)

    def record_failure(self, key, window):
        now = time.monotonic()
        with self._lock:
            self._prune(key, window, now)
            self._failures[key].append(now)

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)


login_limiter = RateLimiter()
