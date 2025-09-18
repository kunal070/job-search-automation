from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple
import time


class RateLimiter:
    def __init__(self, max_per_minute: int = 60, max_per_day: int = 5000) -> None:
        self.max_per_minute = max_per_minute
        self.max_per_day = max_per_day
        self._minute: Deque[float] = deque()
        self._day: Deque[float] = deque()

    def allow(self) -> bool:
        now = time.time()
        while self._minute and now - self._minute[0] >= 60:
            self._minute.popleft()
        while self._day and now - self._day[0] >= 86400:
            self._day.popleft()
        return len(self._minute) < self.max_per_minute and len(self._day) < self.max_per_day

    def record(self) -> None:
        ts = time.time()
        self._minute.append(ts)
        self._day.append(ts)


class SimpleCache:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl = ttl_seconds
        self._data: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._data.get(key)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > self.ttl:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.time(), value)

