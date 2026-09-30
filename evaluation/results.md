# Evaluation results

## Sentiment benchmark

Primary metric: **negative-class F1**. Mixed items are excluded from 3-class metrics.

Evaluable n: **114**; mixed excluded: **36**; neutral support: **3**.

The neutral class is too small for a reliable standalone performance conclusion.

Decision rule fixed before running: Switch only if an eligible licence-clean 3-class challenger has a paired-bootstrap negative-F1 difference CI excluding zero in its favour; on a tie keep the better-documented model.

| Model | Revision | Size MiB | Accuracy (95% CI) | Macro-F1 (95% CI) | Negative F1 (95% CI) | Reweighted accuracy | CPU ms / 100 |
| --- | --- | ---: | --- | --- | --- | ---: | ---: |
| shipping_cardiffnlp | 3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7 | 479.1 | 0.930 [0.868, 0.965] | 0.752 [0.641, 0.905] | 0.968 [0.917, 0.992] | 0.932 | 5004.4 |
| tabularisai_robust_sentiment | c542a281e22b3d840a0b3f6c129acf8e357aed50 | 256.3 | 0.851 [0.781, 0.912] | 0.643 [0.586, 0.795] | 0.919 [0.855, 0.960] | 0.873 | 5289.4 |
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

Evaluation not run: units_gold.csv missing.

## Theme threshold

Evaluation not run: pairs_gold.csv missing.
