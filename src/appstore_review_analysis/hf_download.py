"""Small scoped guard for expected Hugging Face public-download noise."""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def suppress_public_download_auth_warning() -> Iterator[None]:
    """Silence the public-repository authentication hint only while downloading.

    The project intentionally downloads public, immutable model revisions without a
    Hugging Face token. Download exceptions still propagate; the prior logger level is
    always restored after the call.
    """

    logger = logging.getLogger("huggingface_hub")
    previous_level = logger.level
    try:
        logger.setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*unauthenticated requests to the HF Hub.*",
            )
            yield
    finally:
        logger.setLevel(previous_level)
