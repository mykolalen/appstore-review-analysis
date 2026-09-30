from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from appstore_review_analysis.evaluation import label_sheet_rows, read_gold_items


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the labelling sheet: all gold items in one seeded order, labels empty."
    )
    parser.add_argument("--gold", type=Path, default=Path("evaluation/gold_items.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/labels_sheet.csv"))
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite existing label sheet: {args.out}")
    items = read_gold_items(args.gold)
    rows = label_sheet_rows(items)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["review_id", "text", "human_label"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"out": str(args.out), "items": len(items)}))


if __name__ == "__main__":
    main()
