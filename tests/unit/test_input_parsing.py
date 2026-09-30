import pytest

from appstore_review_analysis.collection.sampling import parse_app_id, resolve_seed
from appstore_review_analysis.collection.storefronts import normalise_country
from appstore_review_analysis.errors import AppError


def test_parse_numeric_and_apple_urls_without_fetching() -> None:
    assert parse_app_id("1459969523") == 1459969523
    assert parse_app_id(1459969523) == 1459969523
    assert (
        parse_app_id("https://apps.apple.com/us/app/nebula-spiritual-guidance/id1459969523?x=1")
        == 1459969523
    )
    assert parse_app_id("https://itunes.apple.com/app/id1459969523") == 1459969523


@pytest.mark.parametrize(
    "value",
    [
        "abc",
        "0",
        "-1",
        "https://example.com/id1459969523",
        # Non-ASCII "digits" pass str.isdigit() but int() rejects them.
        "\u00b2",
        "\u2460",
        "1" * 5000,
        "https://[::1/app/id1",
        "https://apps.apple.com/us/app/x/id0",
        "https://apps.apple.com/us/app/x/id" + "1" * 5000,
        True,
    ],
)
def test_invalid_app_is_rejected(value: str | bool) -> None:
    with pytest.raises(AppError) as exc_info:
        parse_app_id(value)
    assert exc_info.value.code == "INVALID_INPUT"


def test_country_validation_and_unverified_storefront() -> None:
    assert normalise_country("US") == "us"
    with pytest.raises(AppError) as exc_info:
        normalise_country("ca")
    assert exc_info.value.code == "STOREFRONT_UNSUPPORTED"


def test_seed_bounds() -> None:
    assert resolve_seed(0) == 0
    assert resolve_seed(2**53 - 1) == 2**53 - 1
    with pytest.raises(AppError):
        resolve_seed(2**53)
