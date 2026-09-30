# Sentiment labeling guideline

Label the **overall sentiment expressed in the review text**, not the star rating and not whether the claim is factually correct. Use exactly one of: `positive`, `negative`, `neutral`, `mixed`.

- **positive** — the review is clearly favorable overall: praise, satisfaction, recommendation, gratitude, or a beneficial outcome dominates.
- **negative** — the review is clearly unfavorable overall: complaint, dissatisfaction, warning, accusation, broken behavior, unwanted charge, or request for remedy dominates.
- **neutral** — there is no clear positive or negative stance, or the text is merely descriptive/unclear/irrelevant.
- **mixed** — meaningful positive and negative sentiment are both present and neither should be discarded to force a single polarity.

Treat sarcasm according to the intended sentiment. Do not infer sentiment from the star rating. A complaint about price or billing is negative even if phrased politely. If a review praises one aspect and complains about another, use `mixed` rather than choosing whichever clause is longer.

## Examples from the committed demo snapshot

### positive
1. “Best palm reading ever — By far the most in-depth palm reading I have ever received!”
2. “Thumbs up — Best experience!”
3. “No longer feel lost — This app has the best features, best customer service, most accuracy, and the monthly subscription cost is nominal.”

### negative
1. “Unauthorized charges — Do not download this app … there’s no way to cancel the subscription …”
2. “Scam — I never subscribed and they keep charging me $50.”
3. “horrible app — that’s it that’s all”

### neutral
1. “Three stars for now, let’s see what this sole mate search reveals..?”
2. “The first One Piece Is The Only One”
3. “First time having my chart done.”

### mixed
1. “Fun guidance — Nebula is a fun insight … The only thing I dislike about it is the cost.”
2. “Lots of info & hidden price — Lots of good insight but also a lot of hidden cost and complicated profile/billing structures.”
3. “Great app some bugs — Hi! I love the app but … after this update i’m not able to open the app.”
