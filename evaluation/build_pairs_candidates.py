from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

import numpy as np

from appstore_review_analysis.analysis.themes import SentenceTransformerEmbedder
from appstore_review_analysis.collection.sampling import sample_ranks
from appstore_review_analysis.evaluation import PAIR_SEED

_BINS = ((0.20, 0.35), (0.35, 0.50), (0.50, 0.65))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build 45 distance-stratified complaint-unit pairs."
    )
    parser.add_argument("--units", type=Path, default=Path("evaluation/units_gold.csv"))
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/pairs_candidates.csv"))
    args = parser.parse_args()
    with args.units.open("r", encoding="utf-8", newline="") as handle:
        complaints = [row for row in csv.DictReader(handle) if row["human_label"] == "complaint"]
    if len(complaints) < 2:
        raise ValueError("at least two human-labelled complaint units are required")
    embedder = SentenceTransformerEmbedder.load(args.models_dir)
    vectors = np.asarray(embedder.encode([row["text"] for row in complaints]), dtype=float)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.where(norms == 0, 1.0, norms)
    candidates: list[dict[str, object]] = []
    for left in range(len(complaints)):
        for right in range(left + 1, len(complaints)):
            distance = float(1.0 - np.dot(vectors[left], vectors[right]))
            candidates.append(
                {
                    "pair_id": f"p:{complaints[left]['id']}::{complaints[right]['id']}",
                    "left_id": complaints[left]["id"],
                    "right_id": complaints[right]["id"],
                    "distance": distance,
                    "text_a": complaints[left]["text"],
                    "text_b": complaints[right]["text"],
                }
            )
    selected: list[dict[str, object]] = []
    rng = random.Random(PAIR_SEED)
    for low, high in _BINS:
        stratum = sorted(
            (row for row in candidates if low <= float(row["distance"]) < high),
            key=lambda row: (float(row["distance"]), str(row["pair_id"])),
        )
        if len(stratum) < 15:
            raise ValueError(f"distance bin [{low:.2f}, {high:.2f}) has only {len(stratum)} pairs")
        indexes = sample_ranks(len(stratum), 15, rng)
        selected.extend(stratum[index] for index in indexes)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pair_id", "left_id", "right_id", "distance", "text_a", "text_b"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(selected)
    print(
        json.dumps(
            {"out": str(args.out), "pairs": len(selected), "bins": [list(item) for item in _BINS]}
        )
    )


if __name__ == "__main__":
    main()
