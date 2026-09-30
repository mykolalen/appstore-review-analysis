from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the pair labelling sheet with the distance hidden (labels empty)."
    )
    parser.add_argument("--candidates", type=Path, default=Path("evaluation/pairs_candidates.csv"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/pairs_label_sheet.csv"))
    args = parser.parse_args()
    with args.candidates.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pair_id", "text_a", "text_b", "human_label"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "pair_id": row["pair_id"],
                    "text_a": row["text_a"],
                    "text_b": row["text_b"],
                    "human_label": "",
                }
            )
    print(json.dumps({"out": str(args.out), "items": len(rows)}))


if __name__ == "__main__":
    main()
