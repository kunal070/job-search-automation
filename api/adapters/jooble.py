from __future__ import annotations

from typing import List
import json
import time
import requests

from .base import JobItem
from .utils import RateLimiter, SimpleCache
from api.settings import settings


class JoobleAdapter:
    source_name = "jooble"

    def __init__(self, cache: SimpleCache, limiter: RateLimiter) -> None:
        self.cache = cache
        self.limiter = limiter

    def _endpoint(self) -> str:
        return f"https://jooble.org/api/{settings.jooble_api_key}"

    def search(self, what: str, where: str, page: int, results_per_page: int) -> List[JobItem]:
        cache_key = f"jooble|{what.lower()}|{where.lower()}|{page}|{results_per_page}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if not settings.jooble_api_key:
            return []

        if not self.limiter.allow():
            return []

        url = self._endpoint()
        payload = {
            "keywords": what,
            "location": where or settings.default_country,
            "page": page,
            "size": results_per_page,
        }

        backoff = 1.0
        for attempt in range(4):
            try:
                resp = requests.post(url, json=payload, timeout=20)
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    if attempt == 3:
                        return []
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and str(retry_after).isdigit() else backoff
                    time.sleep(wait)
                    backoff *= 2
                    continue
                resp.raise_for_status()
                data = resp.json() or {}
                self.limiter.record()
                results = data.get("jobs") or data.get("results") or []
                items: List[JobItem] = []
                for it in results:
                    title = it.get("title") or ""
                    company = it.get("company") or ""
                    location = it.get("location") or (it.get("city") or "")
                    description = it.get("snippet") or it.get("description") or ""
                    url = it.get("link") or it.get("url") or ""
                    created = it.get("updated") or it.get("created") or None
                    items.append(JobItem(title, company, location, description, url, created, self.source_name))
                self.cache.set(cache_key, items)
                return items
            except (requests.RequestException, json.JSONDecodeError):
                if attempt == 3:
                    return []
                time.sleep(backoff)
                backoff *= 2

        return []

