import pytest

from appstore_review_analysis.collection.itunes import ITunesProvider
from appstore_review_analysis.config import Settings


@pytest.mark.live
@pytest.mark.parametrize("country", ["us", "gb"])
def test_nebula_itunes_endpoints_live(country: str) -> None:
    with ITunesProvider(Settings(http_max_retries=3)) as provider:
        app = provider.app_info(1459969523, country)
        population = provider.population(1459969523, country)
        result = provider.sample(1459969523, country, 1, 42)

    assert app.name
    assert population.total_written_reviews > 0
    assert population.reachable > 0
    assert result.sampling.actual == 1
    assert result.reviews[0].source_review_id
