"""In-memory sliding-window limiter for failed login attempts."""

import time
from collections import OrderedDict, deque
from typing import Deque, Optional

MAX_TRACKED_KEYS = 10_000


class LoginRateLimiter:
    """Counts failed attempts per key (IP or NetID) inside a sliding window."""

    def __init__(self) -> None:
        self._failures: "OrderedDict[str, Deque[float]]" = OrderedDict()

    def _prune(self, key: str, window_seconds: int, now: float) -> Deque[float]:
        attempts = self._failures.get(key)
        if attempts is None:
            return deque()
        while attempts and now - attempts[0] >= window_seconds:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
        return attempts

    def retry_after(self, key: str, max_failures: int, window_seconds: int) -> Optional[int]:
        """Seconds until the key may try again, or None if it is not blocked."""
        now = time.monotonic()
        attempts = self._prune(key, window_seconds, now)
        if len(attempts) < max_failures:
            return None
        return max(1, int(window_seconds - (now - attempts[0])) + 1)

    def record_failure(self, key: str) -> None:
        attempts = self._failures.setdefault(key, deque())
        attempts.append(time.monotonic())
        self._failures.move_to_end(key)
        while len(self._failures) > MAX_TRACKED_KEYS:
            self._failures.popitem(last=False)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)

    def clear(self) -> None:
        self._failures.clear()


login_rate_limiter = LoginRateLimiter()
