from __future__ import annotations

from typing import List
import json
import time
import requests

from .base import JobItem
from .utils import RateLimiter, SimpleCache
from api.settings import settings


class AdzunaAdapter:
    source_name = "adzuna"

    def __init__(self, cache: SimpleCache, limiter: RateLimiter) -> None:
        self.cache = cache
        self.limiter = limiter

    def _endpoint(self, page: int) -> str:
        country = settings.adzuna_country_code
        return f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

    def search(self, what: str, where: str, page: int, results_per_page: int) -> List[JobItem]:
        cache_key = f"adzuna|{what.lower()}|{where.lower()}|{page}|{results_per_page}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            return []

        if not self.limiter.allow():
            return []

        params = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "what": what,
            "where": where or settings.default_country,
            "results_per_page": str(results_per_page),
            "max_days_old": "14",
            "sort_by": "date",
        }

        url = self._endpoint(page)

        backoff = 1.0
        for attempt in range(4):
            try:
                resp = requests.get(url, params=params, timeout=20)
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
                for it in data.get("results", []):
                    title = it.get("title") or ""
                    company = (it.get("company") or {}).get("display_name") or ""
                    area = (it.get("location") or {}).get("area") or []
                    city = area[-1] if area else ""
                    location = ", ".join([p for p in [city, settings.adzuna_country_code.upper()] if p])
                    description = it.get("description") or ""
                    url = it.get("redirect_url") or ""
                    created = it.get("created") or None
                    items.append(JobItem(title, company, location, description, url, created, self.source_name))
                self.cache.set(cache_key, items)
                return items
            except (requests.RequestException, json.JSONDecodeError):
                if attempt == 3:
                    return []
                time.sleep(backoff)
                backoff *= 2

        return []

