"""In-memory TTL cache per data source."""
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CacheEntry:
    data: Any
    timestamp: float
    stale: bool = False


class TTLCache:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self._entry: Optional[CacheEntry] = None

    def get(self) -> Optional[CacheEntry]:
        if self._entry is None:
            return None
        age = time.time() - self._entry.timestamp
        if age > self.ttl:
            self._entry.stale = True
        return self._entry

    def set(self, data: Any) -> None:
        self._entry = CacheEntry(data=data, timestamp=time.time(), stale=False)

    def invalidate(self) -> None:
        self._entry = None

    @property
    def last_updated(self) -> Optional[float]:
        return self._entry.timestamp if self._entry else None

    @property
    def is_stale(self) -> bool:
        entry = self.get()
        return entry.stale if entry else True

    @property
    def age_seconds(self) -> Optional[float]:
        if self._entry is None:
            return None
        return time.time() - self._entry.timestamp
