from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from appstore_review_analysis.evaluation import category_audit_metrics, render_results_markdown

_EXPECTED_FIELDS = ["category", "review_id", "matched_phrase", "sentence", "human_label"]


def _section(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"status": "not_run", "reason": "category_audit_sheet.csv missing"}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != _EXPECTED_FIELDS:
            raise ValueError("category audit sheet has an unexpected schema")
        rows = list(reader)
    if not rows:
        return {"status": "not_run", "reason": "category_audit_sheet.csv is empty"}
    return category_audit_metrics(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate category audit labels and update evaluation results."
    )
    parser.add_argument("--sheet", type=Path, default=Path("evaluation/category_audit_sheet.csv"))
    parser.add_argument("--results", type=Path, default=Path("evaluation/results.json"))
    parser.add_argument("--markdown", type=Path, default=Path("evaluation/results.md"))
    args = parser.parse_args()

    results: dict[str, Any]
    if args.results.exists():
        loaded = json.loads(args.results.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("evaluation results must be a JSON object")
        results = loaded
    else:
        results = {}

    section = _section(args.sheet)
    results["issue_categories"] = section
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args.markdown.write_text(render_results_markdown(results), encoding="utf-8", newline="\n")
    print(json.dumps({"results": str(args.results), "issue_categories": section["status"]}))


if __name__ == "__main__":
    main()
