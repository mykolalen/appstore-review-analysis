from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

from appstore_review_analysis.analysis.themes import split_sentences
from appstore_review_analysis.collection.sampling import sample_ranks
from appstore_review_analysis.evaluation import GOLD_SEED, read_gold_items


def _final_labels(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            result[str(row["review_id"]).strip()] = str(row["human_label"]).strip().lower()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build sentence candidates for complaint-unit evaluation."
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/gold_items.csv"))
    parser.add_argument("--labels", type=Path, default=Path("evaluation/labels_final.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/units_candidates.csv"))
    args = parser.parse_args()
    items = read_gold_items(args.gold)
    labels = _final_labels(args.labels)
    by_id = {item.review_id: item for item in items}
    if set(labels) != set(by_id):
        raise ValueError("labels_final.csv must match gold_items.csv")
    selected_ids = {item_id for item_id, label in labels.items() if label in {"negative", "mixed"}}
    positives = sorted(
        (item for item in items if labels[item.review_id] == "positive"),
        key=lambda item: item.review_id,
    )
    positive_indexes = sample_ranks(
        len(positives), min(20, len(positives)), random.Random(GOLD_SEED)
    )
    selected_ids.update(positives[index].review_id for index in positive_indexes)
    rows: list[dict[str, object]] = []
    for item_id in sorted(selected_ids):
        item = by_id[item_id]
        for sentence_index, sentence in enumerate(split_sentences(item.text)):
            rows.append(
                {
                    "id": f"{item.review_id}:s{sentence_index}",
                    "review_id": item.review_id,
                    "star": item.star,
                    "review_gold_label": labels[item.review_id],
                    "text": sentence,
                }
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "review_id", "star", "review_gold_label", "text"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"out": str(args.out), "reviews": len(selected_ids), "sentences": len(rows)}))


if __name__ == "__main__":
    main()
