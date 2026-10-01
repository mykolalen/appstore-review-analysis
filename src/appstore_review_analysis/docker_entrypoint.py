"""Small container launcher that honours the container PORT configuration."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Run the API on all interfaces with exactly one worker."""

    raw_port = os.environ.get("PORT", "8080")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"PORT must be an integer, got {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"PORT must be between 1 and 65535, got {port}")
    uvicorn.run(
        "appstore_review_analysis.main:app",
        host="0.0.0.0",
        port=port,
        workers=1,
    )


if __name__ == "__main__":
    main()
