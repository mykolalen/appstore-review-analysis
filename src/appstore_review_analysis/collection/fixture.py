"""Zero-network replay provider for committed and test snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from appstore_review_analysis.domain import AppInfo, CollectionResult, PopulationInfo
from appstore_review_analysis.errors import AppError


class FixtureProvider:
    """Replay a previously recorded CollectionResult from disk."""

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = fixture_dir

    def _snapshots(self) -> list[tuple[Path, CollectionResult]]:
        snapshots: list[tuple[Path, CollectionResult]] = []
        if not self.fixture_dir.exists():
            return snapshots
        for path in sorted(self.fixture_dir.glob("*.snapshot.json")):
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            snapshots.append((path, CollectionResult.model_validate(payload)))
        return snapshots

    def app_info(self, app_id: int, country: str) -> AppInfo:
        for _path, snapshot in self._snapshots():
            if snapshot.app.app_id == app_id and snapshot.app.country == country:
                return snapshot.app
        raise self._not_available(app_id, country, None, None)

    def population(self, app_id: int, country: str) -> PopulationInfo:
        for _path, snapshot in self._snapshots():
            if snapshot.app.app_id == app_id and snapshot.app.country == country:
                sampling = snapshot.sampling
                return PopulationInfo(
                    total_written_reviews=sampling.population_total,
                    rating_count=sampling.store_rating_count,
                    rating_count_list=sampling.store_histogram,
                    store_mean=sampling.store_mean,
                    reachable=sampling.reachable,
                    frame_first_date=sampling.frame_first_date,
                    frame_last_date=sampling.frame_last_date,
                )
        raise self._not_available(app_id, country, None, None)

    def sample(
        self,
        app_id: int,
        country: str,
        sample_size: int,
        seed: int,
    ) -> CollectionResult:
        for _path, snapshot in self._snapshots():
            if (
                snapshot.app.app_id == app_id
                and snapshot.app.country == country
                and snapshot.sampling.seed == seed
                and snapshot.sampling.requested == sample_size
            ):
                return snapshot.model_copy(deep=True)
        raise self._not_available(app_id, country, sample_size, seed)

    def _not_available(
        self,
        app_id: int,
        country: str,
        sample_size: int | None,
        seed: int | None,
    ) -> AppError:
        available = [
            {
                "app_id": snapshot.app.app_id,
                "country": snapshot.app.country,
                "sample_size": snapshot.sampling.requested,
                "seed": snapshot.sampling.seed,
            }
            for _path, snapshot in self._snapshots()
        ]
        return AppError(
            status_code=404,
            code="FIXTURE_NOT_AVAILABLE",
            message="No recorded fixture matches this collection request.",
            details={
                "requested": {
                    "app_id": app_id,
                    "country": country,
                    "sample_size": sample_size,
                    "seed": seed,
                },
                "available": available,
            },
        )
