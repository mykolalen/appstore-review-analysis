from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from appstore_review_analysis.evaluation import UNIT_LABELS


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate unit labels and write units_gold.csv.")
    parser.add_argument("--sheet", type=Path, default=Path("evaluation/units_label_sheet.csv"))
    parser.add_argument("--candidates", type=Path, default=Path("evaluation/units_candidates.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/units_gold.csv"))
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite unit gold: {args.out}")
    with args.candidates.open("r", encoding="utf-8", newline="") as handle:
        candidates = {row["id"]: row for row in csv.DictReader(handle)}
    labels: dict[str, str] = {}
    with args.sheet.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            item_id = str(row["id"]).strip()
            label = str(row["human_label"]).strip().lower()
            if item_id in labels or item_id not in candidates or label not in UNIT_LABELS:
                raise ValueError(f"invalid unit label row for {item_id}")
            labels[item_id] = label
    if set(labels) != set(candidates):
        raise ValueError("unit label sheet must contain every candidate exactly once")
    fields = ["id", "review_id", "star", "review_gold_label", "text", "human_label"]
    with args.out.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item_id in sorted(candidates):
            row = candidates[item_id]
            writer.writerow({**row, "human_label": labels[item_id]})
    print(json.dumps({"out": str(args.out), "items": len(labels)}))


if __name__ == "__main__":
    main()
