# Architecture

## Data flow

```mermaid
flowchart LR
    C[CLI / REST client] --> V[Input validation]
    V --> P[Provider: iTunes, RSS fallback or fixture]
    P --> S[Uniform rank sampler + D16 sanitizer]
    S --> T[Text preprocessing + language gate]
    T --> M[Ratings + local sentiment]
    M --> K[Negative phrase tables]
    M --> U[Negative sentence units]
    U --> E[MiniLM embeddings]
    E --> H[Agglomerative themes + evidence]
    H --> I[Deterministic evidence-backed insights]
    I --> DB[(SQLite)]
    I --> R[Markdown report + 4 charts]
    DB --> A[GET metrics / insights / review export]
```

## Runtime components

```mermaid
flowchart TB
    subgraph Process[One API process / one uvicorn worker]
        API[FastAPI routes]
        LOCK[Process-wide model inference lock]
        SENT[CardiffNLP RoBERTa]
        EMB[all-MiniLM-L6-v2]
        REPO[SQLAlchemy repository]
        API --> LOCK
        LOCK --> SENT
        LOCK --> EMB
        API --> REPO
    end
    REPO --> SQLITE[(SQLite)]
    API --> APPLE[Legacy iTunes demo provider]
    API --> RSS[Explicit RSS newest-frame fallback]
    API --> FIX[Committed fixture provider]
```

## Trust boundaries

1. **User input** is validated before provider use: app ID/URL, country, sample size, seed and optional recent-window size.
2. **Apple rows** are untrusted external data. `sanitize_row()` strips everything except the review
   allowlist before persistence, export or analysis.
3. **Review text** is untrusted model input. The local models have no tools.
4. **Persistence/export** contains no reviewer nickname or profile URL.

## Determinism and reproducibility

- Sampling uses Floyd's algorithm over `random.Random(seed).random()`.
- The demo fixture records the exact ranks/replacements and replays with zero network access.
- Local model revisions are pinned and loaded offline after download.
- Bootstrap helpers are seeded.
- Report rendering consumes committed JSON inputs and has a golden test.
- Model floats are compared with tolerance (`1e-6`) across platforms; bit-identical PyTorch output is
  not claimed.

## Failure model

The API is synchronous by design for the take-home workload. Provider reads are the only operations
retried. Invalid input is never retried. Provider rate-limit, timeout, unavailable-feed and protocol errors have distinct
codes. Public mode also applies bounded in-memory POST token buckets. Missing local models make readiness fail and analyses that require them return
`MODEL_UNAVAILABLE`; there is no star/rule fallback for sentiment.
