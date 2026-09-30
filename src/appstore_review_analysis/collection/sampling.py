"""Deterministic sampling helpers."""

from __future__ import annotations

import random
import re
import secrets
from urllib.parse import urlparse

from appstore_review_analysis.errors import AppError

MAX_JSON_SAFE_SEED = 2**53
# App Store ids are positive ASCII integers that fit a signed 64-bit column. The ASCII class
# matters: str.isdigit() also accepts superscript or circled digits, which int() rejects.
_APP_ID_RE = re.compile(r"[0-9]{1,19}")
_APP_PATH_RE = re.compile(r"/id([0-9]{1,19})(?:[/?#]|$)")
_MAX_APP_ID = 2**63 - 1


def parse_app_id(value: str | int) -> int:
    """Parse a numeric App Store id or an Apple App Store URL without fetching it."""

    if isinstance(value, bool):
        raise _invalid_app(value)
    if isinstance(value, int):
        return _positive_app_id(value, value)

    raw = str(value).strip()
    if _APP_ID_RE.fullmatch(raw):
        return _positive_app_id(int(raw), value)

    try:
        parsed_url = urlparse(raw)
        host = (parsed_url.hostname or "").lower()
    except ValueError:
        raise _invalid_app(value) from None
    if parsed_url.scheme not in {"http", "https"}:
        raise _invalid_app(value)
    if host not in {"apps.apple.com", "itunes.apple.com"}:
        raise _invalid_app(value)
    match = _APP_PATH_RE.search(parsed_url.path)
    if not match:
        raise _invalid_app(value)
    return _positive_app_id(int(match.group(1)), value)


def _positive_app_id(parsed: int, value: object) -> int:
    if 0 < parsed <= _MAX_APP_ID:
        return parsed
    raise _invalid_app(value)


def _invalid_app(value: object) -> AppError:
    return AppError(
        status_code=422,
        code="INVALID_INPUT",
        message="app must be a positive numeric App Store id or an Apple App Store URL.",
        details={"app": str(value)},
    )


def resolve_seed(seed: int | None) -> int:
    """Validate or create a JavaScript/JSON-safe reproducibility seed."""

    resolved = secrets.randbelow(MAX_JSON_SAFE_SEED) if seed is None else seed
    if not 0 <= resolved < MAX_JSON_SAFE_SEED:
        raise AppError(
            status_code=422,
            code="INVALID_INPUT",
            message="seed must satisfy 0 <= seed < 2**53.",
            details={"seed": resolved},
        )
    return resolved


def validate_sample_size(sample_size: int) -> int:
    """Validate the supported sample-size contract."""

    if not 1 <= sample_size <= 200:
        raise AppError(
            status_code=422,
            code="INVALID_INPUT",
            message="sample_size must be between 1 and 200.",
            details={"sample_size": sample_size},
        )
    return sample_size


def sample_ranks(population: int, k: int, rng: random.Random) -> list[int]:
    """Uniform sample without replacement via Floyd's algorithm.

    Only ``rng.random()`` is consumed, which is Python's stable cross-version stream.
    """

    if population < 0 or k < 0:
        raise ValueError("population and k must be non-negative")
    if population == 0 or k == 0:
        return []
    if k >= population:
        return list(range(population))

    chosen: set[int] = set()
    for j in range(population - k, population):
        t = int(rng.random() * (j + 1))
        chosen.add(j if t in chosen else t)
    return sorted(chosen)


def draw_replacement_rank(
    population: int,
    used: set[int],
    rng: random.Random,
) -> int | None:
    """Draw one unused replacement rank from the same RNG stream."""

    if len(used) >= population:
        return None
    while True:
        candidate = int(rng.random() * population)
        if candidate not in used:
            return candidate
