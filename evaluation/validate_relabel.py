from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from appstore_review_analysis.evaluation import GOLD_LABELS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate relabel_sheet.csv and write labels_relabel.csv."
    )
    parser.add_argument("--sheet", type=Path, default=Path("evaluation/relabel_sheet.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/labels_relabel.csv"))
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"refusing to overwrite relabel results: {args.out}")
    rows: list[dict[str, str]] = []
    with args.sheet.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["review_id", "text", "human_label"]:
            raise ValueError("relabel_sheet.csv has an unexpected schema")
        for row in reader:
            label = str(row["human_label"]).strip().lower()
            if label not in GOLD_LABELS:
                raise ValueError(f"invalid relabel for {row['review_id']}")
            rows.append({"review_id": str(row["review_id"]).strip(), "human_label": label})
    if len(rows) != 30 or len({row["review_id"] for row in rows}) != 30:
        raise ValueError("relabel sheet must contain 30 unique items")
    with args.out.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["review_id", "human_label"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"out": str(args.out), "items": len(rows)}))


if __name__ == "__main__":
    main()
