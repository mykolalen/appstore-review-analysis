# App Store Review Analysis API

A reproducible Python service that collects a uniform random sample of written App Store reviews,
computes rating and sentiment metrics, surfaces negative phrases and complaint themes, and exposes the
result through a REST API and downloadable review exports.

**Architecture in five lines**

1. `collection/` validates the target and provides iTunes rank sampling, an explicit RSS fallback, and fixture replay.
2. `text.py` normalises review text and applies the language/analysis eligibility rules.
3. `analysis/` computes rating metrics, local sentiment, negative phrases, themes and grounded evidence.
4. `api/` + `storage.py` expose synchronous REST endpoints backed by SQLite; `report/` renders the reproducible demo report.

## Requirements mapped to the repository

| Take-home requirement | Implementation | Proof |
|---|---|---|
| **Collect 100 random reviews** | `reviews collect`, `collection/itunes.py`, explicit `collection/rss.py` fallback, Floyd sampler | sampler/provider tests + recorded Nebula snapshot |
| Error handling | `errors.py`, provider retry/error mapping | API + provider contract tests |
| Extract title, text and rating | `domain.Review`, `sanitize_row()` | contract + privacy tests |
| Clean/preprocess text | `text.py` | `tests/unit/test_text.py` |
| Average/distribution of ratings | `analysis/metrics.py` | reference/Wilson tests + report charts |
| Positive/neutral/negative sentiment | pinned CardiffNLP RoBERTa in `analysis/sentiment.py` | mapping + real-model slow test |
| Common negative keywords/phrases | common n-grams + Fightin' Words in `analysis/keywords.py` | keyword tests + report tables |
| Areas of improvement | complaint units, MiniLM clustering, evidence in `themes.py` / `evidence.py` | theme/evidence tests + report |
| Collect endpoint | `POST /v1/analyses` | API tests |
| Metrics / insights endpoints | `GET /v1/analyses/{id}`, `/metrics`, `/insights` | API tests |
| Raw review download | `GET /v1/analyses/{id}/reviews?format=csv|json` | export/API tests |
| Sample report | [`reports/nebula_us_seed42.md`](reports/nebula_us_seed42.md) | golden + fixture-reproduction tests |
| Visualisations | `reports/charts/` | four chart assertions |
| Local setup/docs | this README + [`docs/architecture.md`](docs/architecture.md) | README smoke workflow |
| Design decisions | [`docs/decisions.md`](docs/decisions.md) | ADR structure test |
| Video demo | Ukrainian narration script in [`docs/video_script_uk.md`](docs/video_script_uk.md); recording link: _to be added_ | link opens while logged out |

## Quickstart

### Supported platforms

The primary path is native `uv` on Windows x64/ARM64, Linux with glibc 2.28+, and macOS 14+ on
Apple Silicon. Docker is the fallback for other hosts. Python 3.13 and `uv >= 0.12` are required by
the lock/tooling contract.

For an installer-based uv installation:

```bash
uv self update
uv --version
```

If uv came from a package manager, update it with that package manager instead. Windows needs no
extra console configuration; the CLI reconfigures its text streams to UTF-8.

### PowerShell

```powershell
uv sync --locked
uv run reviews download-models

uv run reviews analyze `
  --provider fixture `
  --snapshot data/fixtures/nebula_us_seed42.snapshot.json `
  --out reports/nebula_us_seed42.analysis.json

uv run reviews report
uv run reviews serve --host 127.0.0.1 --port 8000
```

In another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz

$body = @{
  app = '1459969523'
  country = 'us'
  sample_size = 100
  seed = 42
  provider = 'fixture'
  analyze = $false
} | ConvertTo-Json

$created = Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/v1/analyses `
  -Method Post `
  -ContentType application/json `
  -Body $body

Invoke-RestMethod "http://127.0.0.1:8000/v1/analyses/$($created.analysis_id)"
Invoke-WebRequest `
  "http://127.0.0.1:8000/v1/analyses/$($created.analysis_id)/reviews?format=csv" `
  -OutFile reviews.csv
```

### Bash / Git Bash

The block below is also executed by CI verbatim in fixture mode. It makes no Apple request.

<!-- readme-smoke:start -->
```bash
set -euo pipefail
uv sync --locked
uv run reviews download-models
uv run reviews analyze \
  --provider fixture \
  --snapshot data/fixtures/nebula_us_seed42.snapshot.json \
  --out /tmp/nebula_us_seed42.analysis.json

DATABASE_URL=sqlite:////tmp/appstore-review-analysis.db \
  uv run reviews serve --host 127.0.0.1 --port 8000 >/tmp/reviews-api.log 2>&1 &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT

ready=0
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
test "$ready" -eq 1
curl -fsS http://127.0.0.1:8000/readyz >/dev/null

created="$(curl -fsS -X POST http://127.0.0.1:8000/v1/analyses \
  -H 'content-type: application/json' \
  --data '{"app":"1459969523","country":"us","sample_size":100,"seed":42,"provider":"fixture","analyze":false}')"
analysis_id="$(printf '%s' "$created" | uv run python -c 'import json,sys; print(json.load(sys.stdin)["analysis_id"])')"
curl -fsS "http://127.0.0.1:8000/v1/analyses/$analysis_id" >/tmp/analysis.json
curl -fsS "http://127.0.0.1:8000/v1/analyses/$analysis_id/reviews?format=csv" >/tmp/reviews.csv
test -s /tmp/analysis.json
test -s /tmp/reviews.csv
```
<!-- readme-smoke:end -->

### One request example

Bash:

```bash
curl --json '{"app":"1459969523","country":"us","sample_size":100,"seed":42,"provider":"fixture","analyze":true}' \
  http://127.0.0.1:8000/v1/analyses
```

PowerShell:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/analyses -Method Post -ContentType application/json -Body (@{app='1459969523'; country='us'; sample_size=100; seed=42; provider='fixture'; analyze=$true} | ConvertTo-Json)
```

## Tests

Fast deterministic gate:

```bash
uv run ruff check
uv run ruff format --check
uv run mypy src
uv run pytest -m "not slow and not live"
```

Real local models:

```bash
uv run pytest tests/slow -m slow -v
```

Live Apple checks are explicitly opt-in:

```bash
uv run pytest -m live
```


## Development

Install the development environment and both default Git hook types:

```bash
uv sync
uv run pre-commit install --install-hooks
```

Run the complete pre-commit-stage gate over the repository at any time:

```bash
uv run pre-commit run --all-files
```

The pre-push hooks run strict mypy over `src` and the fast offline pytest suite. Run them explicitly with:

```bash
uv run pre-commit run --all-files --hook-stage pre-push
```

`detect-secrets` uses `.secrets.baseline` plus repository exclusions for generated/verbatim data. If an
intentional high-entropy constant is a false positive, keep the value visible to review and annotate that
source line with `# pragma: allowlist secret`; do not add a real secret to the baseline.

On Windows, regenerate the baseline from Git-tracked paths so repository-style `/` paths are used
consistently. The `[.]` spelling avoids storing backslashes in the portable baseline:

```powershell
$exclude = '^(uv[.]lock|data/|reports/|evaluation/(.*[.]csv|results[.](json|md))|tests/fixtures/)'
$files = @(git ls-files | Where-Object { $_ -notmatch $exclude -and $_ -ne ".secrets.baseline" })

uvx --from detect-secrets==1.5.0 detect-secrets scan `
    --baseline .secrets.baseline `
    --exclude-files $exclude `
    @files
```

Do not use PowerShell `>` redirection to generate the baseline. After regeneration, confirm that
`"results": {}` and that the baseline contains no platform-specific paths. The exclusions deliberately
keep generated reports, evaluation outputs, fixtures and `uv.lock` out of the baseline so paths remain
portable across Windows and Linux.

For an additional local staged scan, install `gitleaks` separately and run the manual hook after staging:

```bash
uv run pre-commit run gitleaks --hook-stage manual
```

The manual hook is intentionally not part of the default reviewer gate; the pre-commit stage itself does
not require any separately installed binary.

## Approach

### Collection and sampling

The demo `itunes` provider uses two legacy Apple/iTunes endpoints: a population endpoint for the
written-review total/histogram and `userReviewsRow` for rank-addressed rows. The provider sends the
same iTunes-client `User-Agent` and `X-Apple-Store-Front` headers used by the verified endpoint.
Sampling is uniform without replacement over the newest reachable written reviews under Apple's
newest-first order. The exact seed, sampled ranks, replacement ranks, population total, reachable
frame and request/retry counts are preserved in the analysis provenance.

Floyd's algorithm uses `random.Random(seed).random()` rather than `random.sample()`. Seeds are kept
below `2**53` so they round-trip safely through JSON/JavaScript tooling. Failed/invalid rows are
replaced deterministically after each wave. Every Apple row is passed through an allowlist before
anything else sees it; nickname/profile fields are never persisted or exported.

For repeatable CI/demo use, `fixture` replays the committed Nebula US sample with **zero network
requests**. The one-off `reviews walk` command validates the sample against the reachable written-review
population; it is not the normal collector.

`rss` is an **explicit fallback provider**, never a silent fallback from `itunes`. It samples uniformly from
the newest reviews exposed by Apple's legacy RSS feed, with a hard frame cap of 500. Empty HTTP 200
feeds are ambiguous on that endpoint, so the provider retries with a fresh cache-busting nonce and returns
`UPSTREAM_UNAVAILABLE` if the feed remains empty. RSS entries without `im:rating` are ignored, and the
provider does not claim its frame statistics are the store-wide rating histogram.

For a recent product-feedback frame, `itunes` also accepts `window_days`. The provider binary-searches
the newest-first rank stream by review date and then applies the same seeded uniform sampler inside that
prefix. For example: `uv run reviews collect --app 1459969523 --country us --n 100 --seed 42
--provider itunes --window-days 90`. The selected frame and its size are recorded in sampling metadata.

To make a separate recent-window report without replacing the committed lifetime demo, record that
collection to an ignored snapshot, analyse the snapshot locally, and render it to separate paths:

```powershell
uv run reviews collect --app 1459969523 --country us --n 100 --seed 42 `
  --provider itunes --window-days 90 `
  --out data/raw/nebula_us_90d.json `
  --record-snapshot data/raw/nebula_us_90d.snapshot.json
uv run reviews analyze --provider fixture `
  --snapshot data/raw/nebula_us_90d.snapshot.json `
  --out reports/nebula_us_90d.analysis.json
uv run reviews report --analysis reports/nebula_us_90d.analysis.json `
  --out reports/nebula_us_90d.md --charts-dir reports/charts/nebula_us_90d
```

The renderer adds a `Recent-window frame` subsection whenever the analysed snapshot carries
`window_days`; no recent-window findings are committed without a separately captured source frame.

### Data-source reality and production boundary

The legacy iTunes endpoints are undocumented and are kept **demo-only**. Apple's current Media
Services terms prohibit automated scraping/extraction/analysis of service content, and Apple's site
terms separately restrict automated acquisition/scraping. The implementation does not attempt to
bypass 403/429 responses and does not use the collected reviews to train a model.

- Apple Media Services terms: https://www.apple.com/legal/internet-services/itunes/at/terms.html
- Apple website terms: https://www.apple.com/legal/internet-services/terms/site.html

For a production system, the intended boundary is different: App Store Connect for reviews of apps
the organisation owns, and a licensed review-data provider for competitor apps. The demo provider
exists to satisfy and make reproducible the take-home's collection requirement, not as a recommendation
for production scraping.

### Text, metrics and uncertainty

`analysis_text` preserves punctuation/emoji for models and evidence. `lexical_text` normalises
apostrophes/contractions while preserving negation. Rating metrics use every sampled review;
sentiment/keyword/theme metrics use reviews that pass the language gate. Every proportion names its
denominator. Wilson 95% intervals are used for proportions, and seeded bootstrap helpers avoid NaN
output for degenerate cases.

### Sentiment

Sentiment runs locally with `cardiffnlp/twitter-roberta-base-sentiment-latest` pinned to revision
`3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7`. The model is downloaded once and loaded offline. Token
truncation is explicit at 512 tokens and labels come from the model's `id2label` mapping. The model is
a tweet-domain model; the project-specific hand-labelled benchmark is reported below, while broader domain transfer remains a limitation.

### Negative phrases and complaint themes

Negative feedback produces two phrase tables from the same counts:

- **common** 1-3 grams ranked by document support;
- **distinctive** 1-3 grams ranked by Fightin' Words log-odds (the z value is a ranking score here,
  not a hypothesis test).

Extraction counts the unfiltered lexical vocabulary so negations remain available to the statistics.
Only presentation is filtered: a shown phrase must contain at least one content token rather than only
stopwords, a bare negation, product-name/generic app terms, or filler/light verbs. Negation is retained
inside substantive phrases such as `not working` or `no refund`. Equal-support shorter n-grams are
subsumed by their longer phrase in the report tables.

Complaint units are negative sentences from any analysable review, not only reviews whose overall
label is negative. A configurable score gate currently requires a negative probability of at least 0.85
for sentences from 4-5 star reviews and 0.50 for 1-3 star reviews. These defaults are heuristics introduced
after observing false-positive negative sentences; they must be re-tuned against hand-labelled complaint
units before being treated as calibrated thresholds. They can be configured with
`UNIT_MIN_NEGATIVE_SCORE_POSITIVE_REVIEWS` and `UNIT_MIN_NEGATIVE_SCORE_OTHER`.

Accepted complaint units are embedded by pinned `all-MiniLM-L6-v2`, then clustered with cosine/average
agglomerative clustering and an adaptive minimum-support policy. The clustering distance threshold is
configurable (`THEME_DISTANCE_THRESHOLD`, default `0.40`). Once labelled unit pairs exist, run
`reviews tune-threshold --pairs evaluation/pairs_gold.csv`; it selects the largest tested threshold whose
same-issue precision is at least 0.80. Until then, `reviews analyze` emits pairwise-distance and cluster-size
diagnostics at 0.30, 0.40, 0.50 and 0.60 without changing the default. Small/unsupported groups remain
explicit outliers, and the report states both unit and complaint-review theme coverage. Every displayed
area of improvement carries source review IDs and evidence excerpts.

## Hand-labelled evaluation

The repository includes a no-API-key human evaluation workflow under `evaluation/`. It builds a
fixed 150-review stratified gold set from the git-ignored population walk. The annotator labels every
review as positive, negative, neutral or mixed following `evaluation/labeling_guideline.md`, and later
re-labels a random 30 with the earlier labels hidden. Exact commands and file expectations are in
[`evaluation/README.md`](evaluation/README.md).

The primary benchmark metric is negative-class F1. Accuracy, fixed-label macro-F1, per-class metrics,
confusion matrices, seeded BCa intervals, prevalence-reweighted accuracy, CPU latency and a small error
analysis are also reported. `mixed` items are stated and excluded from the 3-class metrics. On the
committed gold set, 114 reviews are evaluable in the 3-class benchmark and 36 are mixed; neutral has
only 3 examples, so no standalone neutral-class conclusion is claimed. The shipping CardiffNLP model
reaches 0.930 accuracy, 0.752 macro-F1 and 0.968 negative-class F1. The pinned Tabularis challenger
reaches 0.851 / 0.643 / 0.919 respectively, and the paired BCa 95% CI for Tabularis minus shipping
negative-F1 is [-0.104, -0.009], so the pre-written switch condition is not met. The star-rating rule is
a floor rather than a selectable model. Full results and intervals are in
[`evaluation/results.md`](evaluation/results.md). The larger binary SiEBERT reference is optional.
Download evaluation comparators with:

```powershell
uv run reviews download-models --eval
# optional large binary reference
uv run reviews download-models --eval --with-siebert
```

Comparator directories contain a revision marker and evaluation refuses to load an unverified pin.
The Tabularis comparator is Apache-2.0 licensed. The pre-written model decision rule is conservative:
switch only when an eligible licence-clean 3-class challenger has a paired-bootstrap negative-F1
difference interval excluding zero in its favour; on a tie, retain the better-documented model.

Complaint-unit and theme-distance evaluations are optional. When their gold files do not exist, both
`evaluation/results.md` and the demo report explicitly state which evaluation was not run.

## API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/analyses` | collect + optionally analyse synchronously; returns `201` and relative `Location` |
| `GET` | `/v1/analyses/{id}` | stored full analysis |
| `GET` | `/v1/analyses/{id}/metrics` | preprocessing, ratings, sentiment and keyword tables |
| `GET` | `/v1/analyses/{id}/insights` | themes + deterministic areas of improvement |
| `GET` | `/v1/analyses/{id}/reviews?format=json|csv` | allowlisted raw/analysed review export |
| `GET` | `/healthz` | liveness |
| `GET` | `/readyz` | database + local-model readiness |

`POST /v1/analyses` accepts `provider=itunes|fixture|rss`; `window_days` is available only with
`provider=itunes`. In public mode, omitted provider values are forced to `fixture`, RSS is rejected, and
the public sample-size/rate limits apply before collection.

All error responses use one envelope: `{"error":{"code":"...","message":"...","request_id":"...","details":{...}}}`.
CSV output follows RFC 4180 and guards spreadsheet-formula prefixes in free-text cells.

## Demo report

The committed report is [`reports/nebula_us_seed42.md`](reports/nebula_us_seed42.md), generated only
from the committed snapshot/analysis/population files. Its four charts are under `reports/charts/`:
rating distribution, sentiment distribution, theme support and rating by period.

Reproduce it without contacting Apple:

```bash
uv run reviews analyze --provider fixture --snapshot data/fixtures/nebula_us_seed42.snapshot.json --out reports/nebula_us_seed42.analysis.json
uv run reviews report
uv run pytest tests/slow/test_fixture_reproduction.py -m slow -v
```

Fixture reproduction preserves counts, labels, ranks, review IDs, CIs and deterministic text. Volatile
analysis IDs/timings differ, and model floats are compared to `1e-6` rather than claimed bit-identical
across platforms.

## Measured numbers

Only numbers observed in executed local gates are reported. Missing measurements are explicitly marked
instead of estimated.

| Measurement | Observed value | Evidence / status |
|---|---:|---|
| Nebula fixture sample size | 100 reviews | committed `seed=42` fixture |
| Native fixture reproduction, n=100 | 21.38 s | Windows 11 / Python 3.13 local slow-test run |
| Fast deterministic gate | 132 passed, 5 deselected in 22.99 s | Windows local gate after the repository audit |
| CardiffNLP model download/reconstruction | about 502 MB | local `reviews download-models` output |
| MiniLM model download/reconstruction | about 91.6 MB | local `reviews download-models` output |
| Native n=200 end-to-end | not measured | no committed n=200 fixture |
| Docker n=100/n=200 stage latency | not measured yet | fill from the Docker gate before claiming a number |
| Docker cold start / peak RSS / image size | not measured yet | Docker gate required |

The analysis JSON itself records per-stage timings and provider request/retry counts for a given run;
those run-specific values are preferable to hard-coding a benchmark from another machine.

## Docker

Build and run:

```bash
docker compose up --build
curl -fsS http://127.0.0.1:8080/readyz
```

The image uses Python 3.13, CPU-only torch from the same uv lock, bakes both pinned model revisions,
runs as a non-root user, and persists only `/app/var` through a named volume. The committed fixture and
report stay in the image.

### Public demo mode

`PUBLIC_MODE=true` turns on the bounded public-demo behaviour without changing the normal local API:

- requests with no provider explicitly selected use `fixture`;
- `sample_size` is capped at 100 and `provider=rss` is disabled;
- POSTs use process-local token buckets: 10 globally and 5 per client per 10 minutes by default;
- the per-client key is the **right-most** `X-Forwarded-For` hop, matching the value appended by the
  trusted Google front end in the intended deployment;
- the committed Nebula analysis is loaded at startup under fixed id
  `62ce32e6-406d-5c6b-b94a-d153e37f78f6`;
- the normal relative `Location` header is retained.

The limits are configurable with `PUBLIC_GLOBAL_POST_LIMIT`, `PUBLIC_CLIENT_POST_LIMIT` and
`PUBLIC_RATE_WINDOW_S`. Analyses created on a public container are SQLite-local and therefore last only
for the lifetime of that instance unless an external persistent database is configured. Deployment and
live front-end-header verification are separate operator steps documented in
[`docs/deploy_cloud_run.md`](docs/deploy_cloud_run.md).

Stop/restart without deleting the named volume:

```bash
docker compose down
docker compose up -d
```

## Design decisions

The decision record is in [`docs/decisions.md`](docs/decisions.md); the component/data-flow view is in
[`docs/architecture.md`](docs/architecture.md). The repository currently does not include:

- a public Cloud Run deployment (the public-mode code and deployment instructions are included);
- completed complaint-unit and labelled theme-pair evaluations;
- a published recording (the Ukrainian narration script is provided);
- RAG, a vector database or agents.

## Limitations and scaling path

- The demo iTunes source is undocumented and unsuitable as a production dependency; current Apple terms
  also make the automated-use restriction explicit.
- The sample is random only within the stated reachable newest-first frame; very large apps can have a
  depth cap.
- Store summary ratings can differ from written-review sample/population ratings because they are
  different populations; the report presents both rather than treating one as ground truth for the other.
- The sentiment model is tweet-domain. On the committed hand-labelled set it performs strongly on the
  primary negative-class F1 metric, but the neutral class has only three examples and the evaluation is
  too small to establish broad domain-general performance.
- SQLite + one uvicorn worker is appropriate for the take-home. A production ingestion system should use
  scheduled ingestion, a durable queue, Postgres, rolling aggregates, alerting and provider-specific
  credentials/secrets.
- The synchronous endpoint is intentional for the small demo job. At larger scale, work should move to a
  durable job/queue model rather than FastAPI background tasks.

## Privacy, licences and attribution

Persisted/exported review data follows an allowlist: review ID, title, body, rating/date, edit/vote
fields, developer-response flag/id/date, and provider-specific app version when present. Nickname,
profile URL and `userProfileId` are deliberately excluded. The repository's committed-data test enforces
that boundary. Evaluation labelling sheets contain only an evaluation ID and review/sentence text; star
rating, provider metadata and model outputs are not included.

Code is MIT licensed; see [`LICENSE`](LICENSE). Model attributions and redistribution notes are in
[`NOTICE`](NOTICE). Model weights are downloaded at setup/build time and are excluded from git.
