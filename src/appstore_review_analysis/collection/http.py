"""HTTP helpers with bounded retries and explicit upstream failure mapping."""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from appstore_review_analysis.errors import AppError

_MAX_BACKOFF_S = 8.0


@dataclass(frozen=True)
class JsonResponseResult:
    """Parsed JSON plus request/retry accounting."""

    data: Any
    requests_made: int
    retries: int


def _retry_after_seconds(headers: Mapping[str, str]) -> int | None:
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return max(0, int(value))


class JsonHttpClient:
    """Small wrapper around an injected httpx.Client."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        max_retries: int,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.client = client
        self.max_retries = max_retries
        self.sleeper = sleeper
        self.jitter = jitter

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float,
        lookup_400_is_invalid: bool = False,
        retry_non_json: bool = False,
    ) -> JsonResponseResult:
        """GET and parse JSON regardless of Content-Type."""

        requests_made = 0
        last_retry_after: int | None = None
        for attempt in range(self.max_retries + 1):
            requests_made += 1
            try:
                response = self.client.get(url, params=params, headers=headers, timeout=timeout)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise AppError(
                    status_code=504,
                    code="UPSTREAM_TIMEOUT",
                    message="Apple review service timed out.",
                ) from exc
            except httpx.HTTPError as exc:
                raise AppError(
                    status_code=502,
                    code="UPSTREAM_PROTOCOL_ERROR",
                    message="Apple review service request failed.",
                ) from exc

            if response.status_code == 400 and lookup_400_is_invalid:
                raise AppError(
                    status_code=422,
                    code="INVALID_INPUT",
                    message="Apple rejected the app/country input.",
                )

            if response.status_code == 429:
                last_retry_after = _retry_after_seconds(response.headers)
                # A long Retry-After would block this synchronous request past its deadline,
                # so only short waits are honoured; longer ones fail fast with the hint.
                wait_is_short = last_retry_after is None or last_retry_after <= _MAX_BACKOFF_S
                if attempt < self.max_retries and wait_is_short:
                    if last_retry_after is not None:
                        self.sleeper(float(last_retry_after))
                    else:
                        self._sleep_backoff(attempt)
                    continue
                raise AppError(
                    status_code=503,
                    code="UPSTREAM_RATE_LIMITED",
                    message="Apple review service is rate limited.",
                    retry_after=last_retry_after,
                )

            if 500 <= response.status_code <= 599:
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise AppError(
                    status_code=502,
                    code="UPSTREAM_PROTOCOL_ERROR",
                    message="Apple review service returned a server error.",
                    details={"status": response.status_code},
                )

            if response.status_code >= 400:
                raise AppError(
                    status_code=502,
                    code="UPSTREAM_PROTOCOL_ERROR",
                    message="Apple review service returned an unexpected response.",
                    details={"status": response.status_code},
                )

            try:
                data = json.loads(response.text)
            except json.JSONDecodeError as exc:
                if retry_non_json and attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise AppError(
                    status_code=502,
                    code="UPSTREAM_PROTOCOL_ERROR",
                    message="Apple review service returned non-JSON content.",
                ) from exc

            return JsonResponseResult(
                data=data,
                requests_made=requests_made,
                retries=requests_made - 1,
            )

        raise AssertionError("retry loop exhausted unexpectedly")

    def _sleep_backoff(self, attempt: int) -> None:
        cap = min(_MAX_BACKOFF_S, float(2**attempt))
        self.sleeper(self.jitter() * cap)
