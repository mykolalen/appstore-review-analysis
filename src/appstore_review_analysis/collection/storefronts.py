"""Verified Apple storefront header values.

Only storefronts that were verified live are listed here.
"""

from appstore_review_analysis.errors import AppError

STOREFRONTS: dict[str, str] = {
    "us": "143441-1,29",
    "gb": "143444-2,29",
}


def normalise_country(country: str) -> str:
    """Validate a two-letter storefront country code."""

    value = country.strip().lower()
    if len(value) != 2 or not value.isalpha():
        raise AppError(
            status_code=422,
            code="INVALID_INPUT",
            message="country must be an ISO alpha-2 code.",
            details={"country": country},
        )
    if value not in STOREFRONTS:
        raise AppError(
            status_code=422,
            code="STOREFRONT_UNSUPPORTED",
            message="The requested storefront has not been verified for this build.",
            details={"country": value, "supported": sorted(STOREFRONTS)},
        )
    return value


def storefront_header(country: str) -> str:
    """Return the verified X-Apple-Store-Front value."""

    return STOREFRONTS[normalise_country(country)]
