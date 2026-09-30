from __future__ import annotations

from transformers.utils import logging as transformers_logging

from appstore_review_analysis.analysis.sentiment import (
    _suppress_expected_transformers_load_report,
)


def test_expected_transformers_load_report_suppression_restores_verbosity() -> None:
    original = transformers_logging.get_verbosity()

    with _suppress_expected_transformers_load_report():
        assert transformers_logging.get_verbosity() == transformers_logging.ERROR

    assert transformers_logging.get_verbosity() == original
