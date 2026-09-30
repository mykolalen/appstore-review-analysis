# Hand-labelled evaluation workflow

This workflow needs **no API key**. The annotator labels every review personally; models are only ever
scored against those labels.

## 1. Build the fixed gold candidates

Input: `data/raw/population_us.jsonl` from `reviews walk` and the committed demo snapshot.

```powershell
uv run python evaluation/build_gold.py
uv run python evaluation/make_label_sheet.py
```

Outputs:
- `evaluation/frame_weights.json` (written-review star shares used for prevalence reweighting)
- `evaluation/gold_items.csv` (150 reviews: 40 / 25 / 25 / 25 / 35 by star; the demo sample is excluded)
- `evaluation/labels_sheet.csv` (all 150 in one seeded order, text only, `human_label` empty)

Read `evaluation/labeling_guideline.md`, then fill **every** `human_label` cell using only `positive`,
`negative`, `neutral` or `mixed`. Then write the final labels:

```powershell
uv run python evaluation/finalize_labels.py
```

Output: `evaluation/labels_final.csv` (`review_id`, `human_label`). The command refuses to overwrite an
existing file or to accept a missing, duplicate or invalid label.

## 2. Run the sentiment benchmark

Download the pinned comparator once:

```powershell
uv run reviews download-models --eval
```

Optional large binary reference:

```powershell
uv run reviews download-models --eval --with-siebert
```

Then run:

```powershell
uv run python evaluation/benchmark.py
```

Or, with the optional binary reference:

```powershell
uv run python evaluation/benchmark.py --with-siebert
```

Outputs:
- `evaluation/results.json`
- `evaluation/results.md`

The benchmark excludes `mixed` items from 3-class metrics, reports the fixed-label
macro-F1/negative-F1/accuracy metrics and seeded BCa intervals, prevalence-reweighted accuracy from
`frame_weights.json`, CPU latency and an error-analysis table. The star-rating rule is a floor, not a
selectable model.

## 3. Later-session relabel check

Do this in a later labelling session, without looking at prior labels:

```powershell
uv run python evaluation/make_relabel_sheet.py
```

Fill the 30 `human_label` cells in `evaluation/relabel_sheet.csv`, then:

```powershell
uv run python evaluation/validate_relabel.py
uv run python evaluation/benchmark.py
```

The rerun adds annotator kappa with a seeded bootstrap CI. It measures the self-consistency of a single
annotator, not agreement between annotators.

## 4. Optional complaint-unit evaluation

After `labels_final.csv` exists:

```powershell
uv run python evaluation/build_units_candidates.py
uv run python evaluation/make_units_sheet.py
```

Fill every `human_label` in `evaluation/units_label_sheet.csv` (`complaint` or `not_complaint`), then:

```powershell
uv run python evaluation/validate_units_gold.py
uv run python evaluation/benchmark.py
```

The results add complaint-unit precision/recall with Wilson intervals, five missed complaints, and recall
diagnostics for mixed and 4-5-star reviews.

## 5. Optional theme-distance pair evaluation

After `units_gold.csv` exists:

```powershell
uv run python evaluation/build_pairs_candidates.py
uv run python evaluation/make_pairs_sheet.py
```

The builder uses only complaint units from the gold set, never the committed demo sample, and samples 15
pairs from each distance band `[0.20,0.35)`, `[0.35,0.50)`, `[0.50,0.65)`. The distance is hidden from the
labelling sheet. Fill every `human_label` in `evaluation/pairs_label_sheet.csv` (`same_issue` or
`different_issue`), then:

```powershell
uv run python evaluation/validate_pairs_gold.py
uv run python evaluation/benchmark.py
uv run reviews tune-threshold --pairs evaluation/pairs_gold.csv
```

The selected threshold is the largest tested grid value whose same-issue precision is at least 0.80. If
none qualifies, no threshold is selected.

## 6. Optional issue-category precision audit

First regenerate the analysis so its retained complaint units and category matches are current, then
create the audit sheet:

```powershell
uv run reviews audit-categories `
  --analysis reports/nebula_us_seed42.analysis.json `
  --out evaluation/category_audit_sheet.csv
```

The sheet contains `category`, `review_id`, `matched_phrase`, `sentence` and an empty `human_label`.
Label every row as `correct` or `incorrect`, then validate it:

```powershell
uv run python evaluation/validate_category_audit.py
```

The validator writes an `issue_categories` section to `evaluation/results.json` and refreshes
`evaluation/results.md` with per-category precision and Wilson 95% intervals. This is a precision audit
only: it does not measure recall and it does not replace the sampling intervals reported for category
shares. If the sheet is absent or still unlabelled, no precision value is claimed.

## 7. Refresh the demo report

```powershell
uv run reviews report
```

If `evaluation/results.json` or an optional evaluation file is missing, the report says that the
corresponding evaluation was not run rather than fabricating a result.
