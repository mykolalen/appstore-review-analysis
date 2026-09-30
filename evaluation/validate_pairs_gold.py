from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from appstore_review_analysis.evaluation import PAIR_LABELS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate human pair labels and write pairs_gold.csv."
    )
    parser.add_argument("--sheet", type=Path, default=Path("evaluation/pairs_label_sheet.csv"))
    parser.add_argument("--candidates", type=Path, default=Path("evaluation/pairs_candidates.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/pairs_gold.csv"))
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite pair gold: {args.out}")
    with args.candidates.open("r", encoding="utf-8", newline="") as handle:
        candidates = {row["pair_id"]: row for row in csv.DictReader(handle)}
    labels: dict[str, str] = {}
    with args.sheet.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pair_id = str(row["pair_id"]).strip()
            label = str(row["human_label"]).strip().lower()
            if pair_id in labels or pair_id not in candidates or label not in PAIR_LABELS:
                raise ValueError(f"invalid pair label row for {pair_id}")
            labels[pair_id] = label
    if set(labels) != set(candidates) or len(labels) != 45:
        raise ValueError("pair label sheet must contain all 45 candidates exactly once")
    fields = ["pair_id", "left_id", "right_id", "distance", "text_a", "text_b", "human_label"]
    with args.out.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for pair_id in sorted(candidates):
            writer.writerow({**candidates[pair_id], "human_label": labels[pair_id]})
    print(json.dumps({"out": str(args.out), "items": len(labels)}))


if __name__ == "__main__":
    main()
