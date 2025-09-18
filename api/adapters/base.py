from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol


@dataclass
class JobItem:
    title: str
    company: str
    location: str
    description: str
    url: str
    posted_at: Optional[str]
    source: str


class JobAdapter(Protocol):
    source_name: str

    def search(self, what: str, where: str, page: int, results_per_page: int) -> List[JobItem]:
        ...

