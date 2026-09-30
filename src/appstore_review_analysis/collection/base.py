"""Review-provider protocol."""

from __future__ import annotations

from typing import Protocol

from appstore_review_analysis.domain import AppInfo, CollectionResult, PopulationInfo


class ReviewProvider(Protocol):
    """Minimal provider contract used by the collection CLI and later API."""

    def app_info(self, app_id: int, country: str) -> AppInfo: ...

    def population(self, app_id: int, country: str) -> PopulationInfo: ...

    def sample(
        self,
        app_id: int,
        country: str,
        sample_size: int,
        seed: int,
    ) -> CollectionResult: ...
