from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

from appstore_review_analysis.collection.sampling import sample_ranks
from appstore_review_analysis.evaluation import RELABEL_SEED, read_gold_items


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a 30-item later-session relabel sheet with prior labels hidden."
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/gold_items.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/relabel_sheet.csv"))
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite existing relabel sheet: {args.out}")
    items = sorted(read_gold_items(args.gold), key=lambda item: item.review_id)
    indexes = sample_ranks(len(items), 30, random.Random(RELABEL_SEED))
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["review_id", "text", "human_label"], lineterminator="\n"
        )
        writer.writeheader()
        for index in indexes:
            writer.writerow(
                {"review_id": items[index].review_id, "text": items[index].text, "human_label": ""}
            )
    print(json.dumps({"out": str(args.out), "items": len(indexes)}))


if __name__ == "__main__":
    main()
