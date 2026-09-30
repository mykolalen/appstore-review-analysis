from __future__ import annotations

import numpy as np
import pytest

from appstore_review_analysis.analysis.themes import SentenceTransformerEmbedder
from appstore_review_analysis.config import Settings

pytestmark = pytest.mark.slow


def test_real_embedder_is_pinned_offline_and_normalized() -> None:
    embedder = SentenceTransformerEmbedder.load(Settings().models_dir)
    vectors = embedder.encode(["subscription refund problem", "the app keeps crashing"])
    assert vectors.shape[0] == 2
    assert vectors.shape[1] > 10
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    assert embedder.model_id == "sentence-transformers/all-MiniLM-L6-v2"
