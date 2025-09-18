from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional
import json
import os


@dataclass
class Settings:
    # Secrets
    jsearch_api_key: str = ""
    jooble_api_key: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    # Runtime config
    default_country: str = "Canada"
    max_results: int = 100
    min_results_primary: int = 40
    cache_ttl_seconds: int = 3600
    rate_limits: Dict[str, Dict[str, int]] = None  # per-source limits

    # Defaults for source behavior
    adzuna_country_code: str = "ca"

    @staticmethod
    def _parse_rate_limits(raw: Optional[str]) -> Dict[str, Dict[str, int]]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            jsearch_api_key=os.getenv("JSEARCH_API_KEY", ""),
            jooble_api_key=os.getenv("JOOBLE_API_KEY", ""),
            adzuna_app_id=os.getenv("ADZUNA_APP_ID", os.getenv("APP_ID", "")),
            adzuna_app_key=os.getenv("ADZUNA_APP_KEY", os.getenv("APP_KEY", "")),
            default_country=os.getenv("DEFAULT_COUNTRY", "Canada"),
            max_results=int(os.getenv("MAX_RESULTS", "100")),
            min_results_primary=int(os.getenv("MIN_RESULTS_PRIMARY", "40")),
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
            rate_limits=cls._parse_rate_limits(os.getenv("RATE_LIMITS_JSON")),
            adzuna_country_code=os.getenv("ADZUNA_COUNTRY_CODE", "ca").lower(),
        )


# A module-level settings instance
settings = Settings.load()

