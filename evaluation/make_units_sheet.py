from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the labelling sheet for complaint sentences (labels left empty)."
    )
    parser.add_argument("--candidates", type=Path, default=Path("evaluation/units_candidates.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/units_label_sheet.csv"))
    args = parser.parse_args()
    with args.candidates.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "text", "human_label"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({"id": row["id"], "text": row["text"], "human_label": ""})
    print(json.dumps({"out": str(args.out), "items": len(rows)}))


if __name__ == "__main__":
    main()
