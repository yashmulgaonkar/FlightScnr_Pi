"""
Unified caching layer for API calls.

Provides:
  - TTLCache: A generic thread-safe time-to-live cache.
  - FR24Cache: FlightRadar24 cache with per-key 90s feed TTL and 30-min flight detail TTL.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    """
    Thread-safe dictionary cache with per-key TTL expiry.

    Usage:
        cache = TTLCache(default_ttl=3600)
        cache.set("key", value)
        hit = cache.get("key")  # returns value or None if expired
    """

    def __init__(self, default_ttl: float = 3600.0):
        """
        :param default_ttl: Default time-to-live in seconds for cached entries.
        """
        self._store: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_ts)
        self._lock = threading.Lock()
        self._default_ttl = default_ttl

    @property
    def default_ttl(self) -> float:
        return self._default_ttl

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a cached value by key.
        Returns None if key doesn't exist or has expired.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry_ts = entry
            if time.time() > expiry_ts:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        Store a value with a TTL.
        :param key: Cache key.
        :param value: Value to store.
        :param ttl: Optional TTL override (seconds). Uses default_ttl if None.
        """
        ttl = ttl if ttl is not None else self._default_ttl
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def has(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        return self.get(key) is not None

    def invalidate(self, key: str) -> None:
        """Remove a specific key from cache."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from cache."""
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        """Return number of entries (including possibly expired ones)."""
        with self._lock:
            return len(self._store)

    def cleanup(self) -> int:
        """Remove expired entries. Returns number of entries removed."""
        now = time.time()
        removed = 0
        with self._lock:
            expired_keys = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired_keys:
                del self._store[k]
                removed += 1
        return removed




class FR24Cache:
    """
    Cache layer for FlightRadar24 API.

    - Live feed (get_flights): cached for 90 seconds.
    - Flight details (per flight_id): cached for 30 minutes.
    - Prevents redundant API calls by checking cache first.
    """

    FEED_TTL = 90.0  # 90 seconds for live feed polling
    FLIGHT_DETAIL_TTL = 1800.0  # 30 minutes for individual flight details
    FEED_POLL_INTERVAL = 90.0  # Minimum 90 seconds between feed polls

    def __init__(self):
        self._feed_cache = TTLCache(default_ttl=self.FEED_TTL)
        self._detail_cache = TTLCache(default_ttl=self.FLIGHT_DETAIL_TTL)
        # Per-key rate limiting: tracks last poll time per cache key
        self._per_key_last_poll: dict[str, float] = {}
        self._per_key_lock = threading.Lock()

    @property
    def feed_cache(self) -> TTLCache:
        return self._feed_cache

    @property
    def detail_cache(self) -> TTLCache:
        return self._detail_cache

    def get_cached_flights(self, cache_key: str) -> Optional[list]:
        """
        Get cached flight list for a given bounds/airline key.
        Returns None if no valid cache entry exists.
        """
        return self._feed_cache.get(cache_key)

    def set_cached_flights(self, cache_key: str, flights: list) -> None:
        """Cache a flight list result."""
        self._feed_cache.set(cache_key, flights)

    def get_cached_flight_details(self, flight_id: str) -> Optional[dict]:
        """
        Get cached flight details for a specific flight.
        Returns None if no valid cache entry exists (or expired).
        """
        return self._detail_cache.get(flight_id)

    def set_cached_flight_details(self, flight_id: str, details: dict) -> None:
        """Cache flight details for a specific flight."""
        self._detail_cache.set(flight_id, details)

    def should_poll_feed(self, cache_key: str) -> bool:
        """Returns True if enough time has elapsed to poll this specific feed key."""
        with self._per_key_lock:
            last = self._per_key_last_poll.get(cache_key, 0.0)
            return (time.time() - last) >= self.FEED_POLL_INTERVAL

    def record_feed_poll(self, cache_key: str) -> None:
        """Record that a feed poll was made for this specific key."""
        with self._per_key_lock:
            self._per_key_last_poll[cache_key] = time.time()

    def reset_feed_key(self, cache_key: str) -> None:
        """Reset rate limit and invalidate cache for a specific feed key."""
        with self._per_key_lock:
            self._per_key_last_poll.pop(cache_key, None)
        self._feed_cache.invalidate(cache_key)

    def make_feed_cache_key(
        self, bounds: Optional[dict] = None, airline: Optional[str] = None
    ) -> str:
        """
        Generate a cache key from the feed query parameters.
        """
        parts = []
        if bounds:
            parts.append(
                f"bounds:{bounds.get('tl_y','')},{bounds.get('tl_x','')},"
                f"{bounds.get('br_y','')},{bounds.get('br_x','')}"
            )
        if airline:
            parts.append(f"airline:{airline.upper()}")
        return "|".join(parts) if parts else "global"

    def cleanup(self) -> None:
        """Remove expired entries from both caches."""
        self._feed_cache.cleanup()
        self._detail_cache.cleanup()
