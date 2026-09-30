from __future__ import annotations

import argparse
import json
from pathlib import Path

from appstore_review_analysis.evaluation import (
    build_gold_items,
    frame_weights,
    read_population,
    write_gold_items,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the deterministic 150-item sentiment gold set."
    )
    parser.add_argument("--population", type=Path, default=Path("data/raw/population_us.jsonl"))
    parser.add_argument(
        "--snapshot", type=Path, default=Path("data/fixtures/nebula_us_seed42.snapshot.json")
    )
    parser.add_argument("--out", type=Path, default=Path("evaluation/gold_items.csv"))
    parser.add_argument("--frame-weights", type=Path, default=Path("evaluation/frame_weights.json"))
    args = parser.parse_args()

    population = read_population(args.population)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    excluded = {str(row["source_review_id"]) for row in snapshot["reviews"]}
    items = build_gold_items(population, excluded_review_ids=excluded)
    write_gold_items(args.out, items)
    weights = frame_weights(population, source_path=args.population)
    args.frame_weights.parent.mkdir(parents=True, exist_ok=True)
    args.frame_weights.write_text(
        json.dumps(weights, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    counts = {star: sum(item.star == star for item in items) for star in range(1, 6)}
    print(
        json.dumps(
            {
                "gold_items": str(args.out),
                "frame_weights": str(args.frame_weights),
                "strata": counts,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
