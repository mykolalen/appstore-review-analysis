# Evaluation results

## Sentiment benchmark

Primary metric: **negative-class F1**. Mixed items are excluded from 3-class metrics.

Evaluable n: **114**; mixed excluded: **36**; neutral support: **3**.

The neutral class is too small for a reliable standalone performance conclusion.

Decision rule fixed before running: Switch only if an eligible licence-clean 3-class challenger has a paired-bootstrap negative-F1 difference CI excluding zero in its favour; on a tie keep the better-documented model.

| Model | Revision | Size MiB | Accuracy (95% CI) | Macro-F1 (95% CI) | Negative F1 (95% CI) | Reweighted accuracy | CPU ms / 100 |
| --- | --- | ---: | --- | --- | --- | ---: | ---: |
| shipping_cardiffnlp | 3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7 | 479.1 | 0.930 [0.868, 0.965] | 0.752 [0.641, 0.905] | 0.968 [0.917, 0.992] | 0.932 | 4141.4 |
| tabularisai_robust_sentiment | c542a281e22b3d840a0b3f6c129acf8e357aed50 | 256.3 | 0.851 [0.781, 0.912] | 0.643 [0.586, 0.795] | 0.919 [0.855, 0.960] | 0.873 | 4447.3 |
| star_rating_floor | fixed-rule-v1 | 0.0 | 0.868 [0.798, 0.921] | 0.660 [0.613, 0.804] | 0.917 [0.850, 0.959] | 0.943 | 0.0 |

Tabularis minus shipping negative-F1 paired BCa 95% CI: [-0.104, -0.009].

Decision-rule switch condition met: **False**.

### shipping_cardiffnlp class metrics

| Class | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| negative | 1.000 | 0.938 | 0.968 | 65 |
| neutral | 0.222 | 0.667 | 0.333 | 3 |
| positive | 0.977 | 0.935 | 0.956 | 46 |

Confusion matrix (rows=true, columns=predicted; negative/neutral/positive):

```json
[[61, 4, 0], [0, 2, 1], [0, 3, 43]]
```

### tabularisai_robust_sentiment class metrics

| Class | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| negative | 0.966 | 0.877 | 0.919 | 65 |
| neutral | 0.083 | 0.333 | 0.133 | 3 |
| positive | 0.907 | 0.848 | 0.876 | 46 |

Confusion matrix (rows=true, columns=predicted; negative/neutral/positive):

```json
[[57, 5, 3], [1, 1, 1], [1, 6, 39]]
```

### star_rating_floor class metrics

| Class | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| negative | 1.000 | 0.846 | 0.917 | 65 |
| neutral | 0.071 | 0.333 | 0.118 | 3 |
| positive | 0.956 | 0.935 | 0.945 | 46 |

Confusion matrix (rows=true, columns=predicted; negative/neutral/positive):

```json
[[55, 10, 0], [0, 1, 2], [0, 3, 43]]
```

### Error analysis

| Review ID | Star | Gold | Shipping | Tabularis | Star floor | Text excerpt |
| --- | ---: | --- | --- | --- | --- | --- |
| 13201661996 | 1 | negative | negative | neutral | negative | Fees. Be careful and read all the fine print. They will come for the money and there's not easy way to cancel it. |
| 11602909364 | 1 | negative | negative | neutral | negative | Probably just for profit?? So I've had Nebula for about a couple years now, in the beginning I loved it, but as time passed on they've been offers to make me buy their sub. Today I went on to look at my MC / midheaven since I forgot it and ALL OF A SUDDEN I can't see my placements??? It's asking to  |
| 6967325874 | 1 | negative | neutral | negative | negative | Virgo was not born in August🤡 Virgo was born on December |
| 9355664082 | 2 | negative | negative | positive | negative | DONT USE TRIAL! I tend to lock my card for any foreseen charges & good thing I did. 1. The 3 day free trial isn’t free, it’s 8.52 cents exactly. 2. After the 3 day trial ends it said that you would be charged 29.99 every 3 months, yet then it changed & said 39.99 on the apple receipt for the 3 day t |
| 7342103309 | 2 | negative | neutral | positive | negative | My zodiac sign doesn’t just change out of nowhere. Being born a Virgo I will stay a Virgo for my entire life it doesn’t just magically change. My birthday is still the same as it was since you know, THE DAY I WAS BORN🤦‍♀️🤦‍♀️ |
| 5780333379 | 2 | negative | negative | positive | negative | Refund please!! I went on the site to get a good view and the trial I signed up for billed me upfront in full with no way to cancel. I can’t get my $119 back since Apple says it’s ineligible for a refund! Don’t sign up unless you can comfortably part with your money in times of Corona! |
| 4821101198 | 2 | negative | neutral | neutral | negative | Expensive for generic astrology. Co—Star is a better app. It’s free and very in depth. The subscription for this app is pretty expensive and the information is generic. |
| 14147041085 | 3 | positive | positive | neutral | neutral | Relations. The sketch looks good |

## Annotator repeatability

Repeated items: **30**.

Cohen's kappa: 1.000.

Seeded bootstrap 95% CI: [1.000, 1.000].

All repeated labels matched the originals, so the empirical bootstrap distribution is degenerate and the interval collapses to the observed kappa.

Session separation is not encoded in labels_relabel.csv; interpret this as repeat-label consistency unless later-session collection timing is documented.

## Complaint-unit check

Evaluated sentences: **473**.

Precision: 0.985 [0.957, 0.995], n=199.

Recall: 0.603 [0.549, 0.655], n=325.

| Subgroup | Complaint recall (95% Wilson CI) |
| --- | --- |
| Mixed reviews | 0.416 [0.325, 0.513], n=101 |
| 4-5 star reviews | 0.071 [0.020, 0.226], n=28 |

Five missed complaints (or all, when fewer than five):

- `10369949410:s0` — SCAM!
- `10369949410:s1` — Hidden charges.
- `10369949410:s5` — Purely and simply.
- `10375806765:s3` — I reverse searched multiple advisors pictures and found that they are used on photoshop websites and similar faces are merged to make different version of the same person.
- `10522378927:s1` — While I do enjoy the app, the advisors section needs major work!

## Theme threshold

Selection rule: largest grid threshold with same-issue precision >= 0.80.

Selected threshold: **none**.

| Threshold | Predicted same | True same | Precision | Eligible |
| ---: | ---: | ---: | ---: | --- |
| 0.20 | 0 | 0 | n/a | False |
| 0.25 | 3 | 2 | 0.667 | False |
| 0.30 | 4 | 3 | 0.750 | False |
| 0.35 | 15 | 8 | 0.533 | False |
| 0.40 | 17 | 9 | 0.529 | False |
| 0.45 | 22 | 10 | 0.455 | False |
| 0.50 | 30 | 12 | 0.400 | False |
| 0.55 | 31 | 12 | 0.387 | False |
| 0.60 | 36 | 12 | 0.333 | False |
| 0.65 | 45 | 14 | 0.311 | False |

## Issue-category precision audit

Human precision audit of lexicon matches only; it does not estimate recall or replace the sampling intervals reported for category prevalence.

| Category | Audited matches | Correct | Precision (95% Wilson CI) |
| --- | ---: | ---: | --- |
| ads_interruptions | 2 | 2 | 1.000 [0.342, 1.000], n=2 |
| billing_charges | 11 | 10 | 0.909 [0.623, 0.984], n=11 |
| bugs_stability | 2 | 2 | 1.000 [0.342, 1.000], n=2 |
| content_accuracy_relevance | 4 | 3 | 0.750 [0.301, 0.954], n=4 |
| customer_support | 5 | 5 | 1.000 [0.566, 1.000], n=5 |
| pricing_paywall | 13 | 9 | 0.692 [0.424, 0.873], n=13 |
| refunds | 5 | 5 | 1.000 [0.566, 1.000], n=5 |
| scam_trust | 11 | 11 | 1.000 [0.741, 1.000], n=11 |
| service_responsiveness | 2 | 0 | 0.000 [0.000, 0.658], n=2 |
| subscription_cancellation | 9 | 6 | 0.667 [0.354, 0.879], n=9 |
