"""Single source of truth for methodology wording used by the demo report."""

from __future__ import annotations

SOURCE_NAME = "Apple iTunes legacy customer-review endpoints"
SOURCE_CAVEAT = (
    "The demo collector uses undocumented Apple iTunes endpoints that require an iTunes-client "
    "User-Agent and X-Apple-Store-Front header. The endpoint is used for this take-home demo only."
)
SAMPLING_METHOD = "uniform random sample without replacement by review rank"
RATING_INTERVAL = "Wilson 95% intervals for proportions; seeded BCa bootstrap for means"
SENTIMENT_MODEL_DOMAIN_CAVEAT = (
    "The sentiment model is derived from TweetEval/TimeLMs and therefore has a tweet-domain caveat."
)
REPRODUCTION_NOTE = (
    "Fixture mode reproduces every count, label, rank, review ID and confidence interval. "
    "Analysis IDs, run timestamps and timings differ. Model scores are "
    "compared to 1e-6 because PyTorch does not promise bit-identical floating-point results across "
    "platforms."
)
