from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from appstore_review_analysis.evaluation import GOLD_LABELS, read_gold_items


def _read_human(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["review_id", "text", "human_label"]:
            raise ValueError("labels_sheet.csv has an unexpected schema")
        for row in reader:
            item_id = str(row["review_id"]).strip()
            label = str(row["human_label"]).strip().lower()
            if item_id in labels or label not in GOLD_LABELS:
                raise ValueError(f"missing, invalid or duplicate human label for {item_id}")
            labels[item_id] = label
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the annotator's labels and write labels_final.csv."
    )
    parser.add_argument("--sheet", type=Path, default=Path("evaluation/labels_sheet.csv"))
    parser.add_argument("--gold", type=Path, default=Path("evaluation/gold_items.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/labels_final.csv"))
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite final labels: {args.out}")
    items = read_gold_items(args.gold)
    human = _read_human(args.sheet)
    if set(human) != {item.review_id for item in items}:
        raise ValueError("labels_sheet.csv must contain every gold item exactly once")
    with args.out.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["review_id", "human_label"], lineterminator="\n"
        )
        writer.writeheader()
        for item in items:
            writer.writerow({"review_id": item.review_id, "human_label": human[item.review_id]})
    print(json.dumps({"out": str(args.out), "items": len(items)}))


if __name__ == "__main__":
    main()
