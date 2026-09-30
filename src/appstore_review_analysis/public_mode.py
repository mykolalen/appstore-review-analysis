"""Public-mode helpers: deterministic seed preload and in-memory POST rate limiting."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi import Request

from appstore_review_analysis.domain import AnalysisPayload

PUBLIC_SEED_NAME = "nebula-us-seed42"
PUBLIC_SEED_ANALYSIS_ID = str(uuid5(NAMESPACE_URL, PUBLIC_SEED_NAME))


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class PublicRateLimiter:
    """Process-local token buckets for the single-instance public demo."""

    def __init__(
        self,
        *,
        global_capacity: int,
        client_capacity: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if global_capacity < 1 or client_capacity < 1 or window_s <= 0:
            raise ValueError("public rate-limit settings must be positive")
        self.global_capacity = global_capacity
        self.client_capacity = client_capacity
        self.window_s = window_s
        self.clock = clock
        now = clock()
        self._global = _Bucket(tokens=float(global_capacity), updated_at=now)
        self._clients: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def consume(self, client_key: str) -> tuple[bool, int]:
        """Consume one global and one per-client token, or return a retry delay."""

        now = self.clock()
        with self._lock:
            global_bucket = self._refill(
                self._global,
                capacity=self.global_capacity,
                now=now,
            )
            client_bucket = self._clients.get(client_key)
            if client_bucket is None:
                client_bucket = _Bucket(tokens=float(self.client_capacity), updated_at=now)
                self._clients[client_key] = client_bucket
            client_bucket = self._refill(
                client_bucket,
                capacity=self.client_capacity,
                now=now,
            )

            retry_s = max(
                self._retry_after(global_bucket, self.global_capacity),
                self._retry_after(client_bucket, self.client_capacity),
            )
            if global_bucket.tokens < 1.0 or client_bucket.tokens < 1.0:
                return False, max(1, math.ceil(retry_s))

            global_bucket.tokens -= 1.0
            client_bucket.tokens -= 1.0
            return True, 0

    def _refill(self, bucket: _Bucket, *, capacity: int, now: float) -> _Bucket:
        elapsed = max(0.0, now - bucket.updated_at)
        if elapsed:
            refill_per_second = capacity / self.window_s
            bucket.tokens = min(float(capacity), bucket.tokens + elapsed * refill_per_second)
            bucket.updated_at = now
        return bucket

    def _retry_after(self, bucket: _Bucket, capacity: int) -> float:
        if bucket.tokens >= 1.0:
            return 0.0
        refill_per_second = capacity / self.window_s
        return (1.0 - bucket.tokens) / refill_per_second


def public_client_key(request: Request) -> str:
    """Use the right-most X-Forwarded-For hop appended by the trusted front end."""

    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1]
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def load_public_seed_analysis(path: Path) -> AnalysisPayload:
    """Load the committed demo analysis and replace its random UUID with a stable UUIDv5."""

    with path.open("r", encoding="utf-8") as handle:
        payload = AnalysisPayload.model_validate(json.load(handle))
    return payload.model_copy(update={"analysis_id": PUBLIC_SEED_ANALYSIS_ID})
