"""Idempotency key store to prevent duplicate destructive action processing.

Duplicate detection works by:
1. Client sends a unique Idempotency-Key header with each mutation request
2. Server stores key → response mapping with a TTL (default 5 minutes)
3. Duplicate requests with the same key return the cached response immediately
4. IdempotencyStore uses an in-process dict with time-based expiration

Thread safety: uses a threading.Lock for all store operations.
"""

import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class IdempotencyEntry:
    """A cached idempotency response entry."""
    response: Any
    created_at: float


class IdempotencyStore:
    """Thread-safe in-memory idempotency key store with TTL expiration."""

    # Default TTL for cached responses (5 minutes)
    DEFAULT_TTL_SECONDS = 300

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: Dict[str, IdempotencyEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """Return cached response for key, or None if missing/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry.created_at > self._ttl:
                del self._store[key]
                return None
            return entry.response

    def set(self, key: str, response: Any) -> None:
        """Cache response for key with current timestamp."""
        with self._lock:
            self._store[key] = IdempotencyEntry(
                response=response,
                created_at=time.monotonic(),
            )

    def get_or_execute(
        self,
        key: str,
        fn: Callable[[], Any],
    ) -> Tuple[Any, bool]:
        """Get cached response for key, or execute fn and cache its result.

        Returns (response, was_cached) — was_cached is True if a cached
        response was returned, False if fn was executed.
        """
        cached = self.get(key)
        if cached is not None:
            return cached, True
        result = fn()
        self.set(key, result)
        return result, False

    def clear_expired(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        removed = 0
        now = time.monotonic()
        with self._lock:
            expired_keys = [
                k for k, entry in self._store.items()
                if now - entry.created_at > self._ttl
            ]
            for k in expired_keys:
                del self._store[k]
                removed += 1
        return removed

    @staticmethod
    def generate_key() -> str:
        """Generate a new UUID4 idempotency key."""
        return str(uuid.uuid4())


# Global singleton store (5-minute TTL)
_global_store: Optional[IdempotencyStore] = None
_global_lock = threading.Lock()


def get_global_store() -> IdempotencyStore:
    """Return the global idempotency store (lazily created)."""
    global _global_store
    with _global_lock:
        if _global_store is None:
            _global_store = IdempotencyStore()
        return _global_store


def reset_global_store() -> None:
    """Reset the global idempotency store (for testing)."""
    global _global_store
    with _global_lock:
        _global_store = None
