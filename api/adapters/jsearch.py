from __future__ import annotations

from typing import List
import json
import time
import requests

from .base import JobItem
from .utils import RateLimiter, SimpleCache
from api.settings import settings


class JSearchAdapter:
    source_name = "jsearch"

    BASE_URL = "https://jsearch.p.rapidapi.com/search"

    def __init__(self, cache: SimpleCache, limiter: RateLimiter) -> None:
        self.cache = cache
        self.limiter = limiter

    def search(self, what: str, where: str, page: int, results_per_page: int) -> List[JobItem]:
        cache_key = f"jsearch|{what.lower()}|{where.lower()}|{page}|{results_per_page}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if not settings.jsearch_api_key:
            return []

        if not self.limiter.allow():
            return []

        headers = {
            "X-RapidAPI-Key": settings.jsearch_api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        }
        query = what
        params = {
            "query": query,
            "page": str(page),
            "num_pages": "1",
            "country": "ca",
        }

        backoff = 1.0
        for attempt in range(4):
            try:
                resp = requests.get(self.BASE_URL, headers=headers, params=params, timeout=20)
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
                items = []
                for it in data.get("data", []):
                    title = it.get("job_title") or ""
                    company = it.get("employer_name") or ""
                    city = it.get("job_city") or ""
                    country = it.get("job_country") or "CA"
                    location = ", ".join([p for p in [city, country] if p])
                    description = it.get("job_description") or ""
                    url = it.get("job_apply_link") or ""
                    created = it.get("job_posted_at_datetime_utc") or None
                    items.append(JobItem(title, company, location, description, url, created, self.source_name))
                self.cache.set(cache_key, items)
                return items
            except (requests.RequestException, json.JSONDecodeError):
                if attempt == 3:
                    return []
                time.sleep(backoff)
                backoff *= 2

        return []

