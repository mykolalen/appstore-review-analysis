from __future__ import annotations

import logging
import warnings

from _pytest.logging import LogCaptureFixture

from appstore_review_analysis.hf_download import suppress_public_download_auth_warning


def test_public_hf_download_warning_guard_restores_logging(
    caplog: LogCaptureFixture,
) -> None:
    logger = logging.getLogger("huggingface_hub")
    original_level = logger.level
    with caplog.at_level(logging.WARNING):
        with suppress_public_download_auth_warning():
            logger.warning("You are sending unauthenticated requests to the HF Hub.")
            warnings.warn(
                "You are sending unauthenticated requests to the HF Hub.",
                RuntimeWarning,
                stacklevel=1,
            )
    assert logger.level == original_level
    assert not any("unauthenticated requests" in record.getMessage() for record in caplog.records)
