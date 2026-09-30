# Architecture Decision Records

These ADRs describe the implementation that is present in this repository.

## ADR-001 - Demo review source

### Context
The task requires collecting 100 random App Store reviews. The official App Store Connect review API is
appropriate for apps an organisation owns, but not arbitrary competitor apps. The take-home needs a
reproducible keyless demonstration.

### Decision
Use the legacy iTunes population + rank endpoints for the **demo provider only**, with explicit iTunes
client headers, no 403/429 evasion, and a committed zero-network fixture for CI/demo reproduction. An
RSS newest-review provider is available only when explicitly requested; it never silently replaces the
iTunes frame, retries ambiguous empty feeds with cache-busting nonces, and labels its newest-500 cap.
The README discloses that these endpoints are legacy/undocumented and that current Apple terms restrict
automated scraping/extraction/analysis.

### Rejected alternatives
RSS newest-500 as the default frame; requiring a young third-party-vendor account; HTML scraping;
silently switching providers after failure.

### Evidence
[`collection/itunes.py`](../src/appstore_review_analysis/collection/itunes.py),
[`collection/rss.py`](../src/appstore_review_analysis/collection/rss.py),
[`tests/contract/test_itunes_provider.py`](../tests/contract/test_itunes_provider.py),
[`tests/contract/test_rss_provider.py`](../tests/contract/test_rss_provider.py), and Apple's
[current Media Services terms](https://www.apple.com/legal/internet-services/itunes/at/terms.html).

## ADR-002 - Sampling frame and seed

### Context
"Random reviews" needs an explicit population and a reproducible draw.

### Decision
Sample uniformly without replacement by rank over the newest reachable written-review frame. Use Floyd's
algorithm driven only by `random.Random(seed).random()`, constrain seeds to `<2**53`, record ranks and
replacements, and keep the full population walk as a one-off validation rather than the collector. When
`window_days` is requested with the iTunes provider, binary-search the newest-first date stream to find
the exact in-window prefix, then apply the same seeded sampler inside that prefix.

### Rejected alternatives
Latest 100 reviews; `random.sample`; treating the RSS newest 500 as equivalent to the full iTunes frame;
walking the full population on every request.

### Evidence
[`collection/sampling.py`](../src/appstore_review_analysis/collection/sampling.py),
[`collection/itunes.py`](../src/appstore_review_analysis/collection/itunes.py),
[`tests/unit/test_sampling.py`](../tests/unit/test_sampling.py), and the recent-window contract test in
[`tests/contract/test_itunes_provider.py`](../tests/contract/test_itunes_provider.py).

## ADR-003 - Sentiment model

### Context
The service needs positive/neutral/negative sentiment without making an LLM call per review.

### Decision
Use `cardiffnlp/twitter-roberta-base-sentiment-latest` pinned at
`3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7`, CPU-only, local/offline after download, explicit
`max_length=512`, and labels from `id2label`. The committed hand-labelled benchmark reports 0.968
negative-class F1 for the shipping model versus 0.919 for the pinned Tabularis challenger; the paired
BCa 95% CI for challenger minus shipping is [-0.104, -0.009], so the pre-written switch rule keeps the
shipping model. The neutral class has only three examples, so the tweet-domain caveat remains.

### Rejected alternatives
Star rating as sentiment; LLM sentiment classification; silently falling back to rules; unpinned model
weights.

### Evidence
[`analysis/sentiment.py`](../src/appstore_review_analysis/analysis/sentiment.py),
[`tests/slow/test_sentiment_model.py`](../tests/slow/test_sentiment_model.py), and [`NOTICE`](../NOTICE).

## ADR-004 - Negative keywords and phrases

### Context
The literal task asks for common negative phrases, but raw frequency alone over-ranks generic language.

### Decision
Return two tables from shared 1-3-gram counts: common phrases by document support and distinctive phrases
by Fightin' Words log-odds. Preserve negations and treat the z value as a ranking score, not a significance
test at this sample size. Keep extraction unfiltered, but filter displayed rows so each phrase contains a
substantive content token; equal-support shorter n-grams are subsumed by a longer phrase for presentation.

### Rejected alternatives
A single frequency list; sklearn's English stoplist (it removes useful negation); word clouds; opaque
KeyBERT-only ranking.

### Evidence
[`analysis/keywords.py`](../src/appstore_review_analysis/analysis/keywords.py) and
[`tests/unit/test_keywords.py`](../tests/unit/test_keywords.py).

## ADR-005 - Complaint themes

### Context
A review can contain a complaint even when its overall sentiment is not negative, and the demo sample has
too few complaint units for a heavy topic-model stack.

### Decision
Classify sentences, apply configurable negative-score gates (0.85 for 4-5 star reviews and 0.50 for
1-3 star reviews by default), embed accepted complaint units with pinned `all-MiniLM-L6-v2`, cluster with
cosine average-linkage agglomerative clustering, use adaptive minimum support, and expose unsupported
groups as outliers. The score defaults are heuristics based on observed classifier errors and are intended
to be re-tuned with hand-labelled complaint units. The clustering threshold remains configurable and can
be selected from labelled unit pairs by requiring same-issue precision of at least 0.80. Attach review
IDs/excerpts and explicit coverage to every displayed theme/area.

### Rejected alternatives
BERTopic; forced-k k-means; clustering whole reviews only; LLM-created counts/taxonomy without evidence.

### Evidence
[`analysis/themes.py`](../src/appstore_review_analysis/analysis/themes.py),
[`analysis/evidence.py`](../src/appstore_review_analysis/analysis/evidence.py), and
[`tests/unit/test_themes.py`](../tests/unit/test_themes.py).

## ADR-006 - No RAG, vector database or agents

### Context
The service analyses one bounded sample at a time; there is no retrieval query over a growing knowledge
base and the model is not expected to take autonomous actions.

### Decision
Keep the pipeline direct: deterministic statistics, local models, clustering and deterministic
evidence-backed insights.

### Rejected alternatives
LangChain/agent orchestration; vector database; RAG over the review sample; multi-agent review analysis.

### Evidence
[`docs/architecture.md`](architecture.md) and [`analysis/pipeline.py`](../src/appstore_review_analysis/analysis/pipeline.py).

## ADR-007 - Synchronous API with deadlines

### Context
The take-home job is bounded and persisted only after successful processing. A background-job subsystem
would add durable-state/worker complexity and new failure modes.

### Decision
Use a synchronous `POST /v1/analyses` with explicit collection/request deadlines and typed degraded/error
outcomes. CPU inference runs in normal synchronous handlers/threadpool paths.

### Rejected alternatives
`202` + FastAPI `BackgroundTasks`; an in-process job table; blocking model inference inside async route
handlers.

### Evidence
[`api/routes.py`](../src/appstore_review_analysis/api/routes.py),
[`analysis/pipeline.py`](../src/appstore_review_analysis/analysis/pipeline.py), and API tests.

## ADR-008 - Runtime and model packaging

### Context
The original PyTorch weights are straightforward and compatible with the pinned transformers stack. The
take-home needs reproducible Windows/Linux operation more than minimum image size.

### Decision
Use Python 3.13 + uv, CPU-only torch as a direct dependency from the PyTorch CPU index, and download pinned
models into `MODELS_DIR`. The Docker image bakes the model files and runs offline. ONNX can be evaluated
later from self-exported pinned weights.

### Rejected alternatives
CUDA wheels; relying on Hugging Face at request time; third-party converted weights; forcing ONNX into the
initial implementation.

### Evidence
[`pyproject.toml`](../pyproject.toml), [`Dockerfile`](../Dockerfile), and the CPU-lock check in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

## ADR-009 - Data publishing and privacy

### Context
App Store provider rows can include stable reviewer/profile identifiers that are unnecessary for the
analysis and inappropriate to publish in a take-home repository.

### Decision
Persist/export/commit only the explicit review allowlist. Never retain nickname (`name`), profile URL or
`userProfileId`. Escape spreadsheet-formula prefixes in text exports. Raw population walks stay ignored;
only aggregate population statistics are committed.

### Rejected alternatives
Committing raw Apple provider rows; keeping profile URLs "just in case"; publishing unrestricted provider
JSON; unescaped CSV text cells.

### Evidence
[`collection/itunes.py`](../src/appstore_review_analysis/collection/itunes.py),
[`export.py`](../src/appstore_review_analysis/export.py), and
[`tests/unit/test_committed_data_allowlist.py`](../tests/unit/test_committed_data_allowlist.py).

## ADR-010 - Human evaluation

### Context
A credible sentiment benchmark needs labels made by a person, not derived from star ratings or from another model. Requiring a live external labelling API would also make the evaluation hard to reproduce.

### Decision
Build a fixed 150-review stratified gold set from the ignored population walk. The annotator labels every review as positive, negative, neutral or mixed following the written guideline, and later re-labels a random 30 with the earlier labels hidden. Benchmark models only against these human labels, excluding `mixed` items from the 3-class metrics and stating their count. Report the annotator's repeat-label kappa, noting that it measures self-consistency of a single annotator, not agreement between annotators.

### Rejected alternatives
Using star ratings as ground truth; a live labelling API (not reproducible without credentials); reporting neutral-class conclusions from three examples.

### Evidence
[`evaluation/README.md`](../evaluation/README.md),
[`evaluation/build_gold.py`](../evaluation/build_gold.py), and
[`evaluation.py`](../src/appstore_review_analysis/evaluation.py).

## ADR-011 - Generic issue categories for actionable findings

### Context
The sentence-clustering layer is intentionally conservative at its untuned default distance threshold,
so semantically related complaints can remain fragmented even when reviewers repeatedly describe the
same operational issue. The take-home also requires actionable areas of improvement, while counts and
causal claims must remain traceable to review evidence.

### Decision
Add a versioned, app-agnostic issue-category layer beside semantic clustering. Match only explicit
lowercase whole-token phrases against retained complaint units, guard negated trust triggers, count each
review at most once per category, allow categories to overlap, and display only categories supported by
at least two complaint reviews. Report support with Wilson 95% sampling intervals, recency, mean stars,
matched phrases, review IDs and excerpts. Pair every category with a static `Check whether ...`
investigation hypothesis rather than asserting a cause. Keep semantic themes unchanged as emerging
clusters, and report uncategorised complaint reviews explicitly. Category precision is reported only
after the optional human audit is labelled and validated.

### Rejected alternatives
Lowering the clustering threshold without labelled pair evidence; learning categories from the demo app;
substring keyword matching; forcing each complaint into exactly one category; treating lexical matches
as causal findings; hiding unmatched complaints.

### Evidence
[`analysis/issue_categories.py`](../src/appstore_review_analysis/analysis/issue_categories.py),
[`analysis/evidence.py`](../src/appstore_review_analysis/analysis/evidence.py), and
[`tests/unit/test_issue_categories.py`](../tests/unit/test_issue_categories.py).
