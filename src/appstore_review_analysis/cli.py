"""Command-line entry point."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from appstore_review_analysis.analysis.pipeline import analyse_collection
from appstore_review_analysis.analysis.sentiment import (
    SentimentAnalyzer,
    TransformerSentiment,
    download_sentiment_model,
)
from appstore_review_analysis.analysis.themes import (
    EmbeddingModel,
    SentenceTransformerEmbedder,
    download_embedding_model,
    select_distance_threshold,
)
from appstore_review_analysis.api.schemas import MAX_WINDOW_DAYS
from appstore_review_analysis.collection.fixture import FixtureProvider
from appstore_review_analysis.collection.itunes import ITunesProvider
from appstore_review_analysis.collection.rss import RSSProvider
from appstore_review_analysis.collection.sampling import (
    parse_app_id,
    resolve_seed,
    validate_sample_size,
)
from appstore_review_analysis.collection.storefronts import normalise_country
from appstore_review_analysis.config import Settings, get_settings
from appstore_review_analysis.domain import CollectionResult, Review
from appstore_review_analysis.errors import AppError
from appstore_review_analysis.evaluation import (
    SIEBERT_MODEL_DIRNAME,
    SIEBERT_MODEL_ID,
    SIEBERT_MODEL_REVISION,
    TABULARIS_MODEL_DIRNAME,
    TABULARIS_MODEL_ID,
    TABULARIS_MODEL_REVISION,
    download_evaluation_model,
)
from appstore_review_analysis.export import write_collection_json, write_reviews_csv
from appstore_review_analysis.report.render import write_report_files
from appstore_review_analysis.storage import StorageRepository


def _configure_stdio() -> None:
    """Force UTF-8 console output on Windows and other non-UTF-8 locales."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


_configure_stdio()

app = typer.Typer(
    name="reviews",
    help="App Store review analysis tools.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """App Store review analysis command-line interface."""


def _provider(name: str, settings: Settings) -> ITunesProvider | FixtureProvider | RSSProvider:
    if name == "itunes":
        return ITunesProvider(settings)
    if name == "fixture":
        return FixtureProvider(settings.fixture_dir)
    if name == "rss":
        return RSSProvider(settings)
    raise AppError(
        status_code=422,
        code="INVALID_INPUT",
        message="provider must be 'itunes', 'fixture', or 'rss'.",
        details={"provider": name},
    )


def _runtime_sentiment(settings: Settings) -> SentimentAnalyzer:
    """Load the pinned sentiment model offline for CLI analysis."""

    try:
        return TransformerSentiment.load(settings.models_dir)
    except Exception as exc:
        raise AppError(
            status_code=503,
            code="MODEL_UNAVAILABLE",
            message="Sentiment model is unavailable. Run 'reviews download-models'.",
            details={"error": type(exc).__name__},
        ) from exc


def _runtime_embedder(settings: Settings) -> EmbeddingModel:
    """Load the pinned MiniLM embedder offline for CLI analysis."""

    try:
        return SentenceTransformerEmbedder.load(settings.models_dir)
    except Exception as exc:
        raise AppError(
            status_code=503,
            code="MODEL_UNAVAILABLE",
            message="Embedding model is unavailable. Run 'reviews download-models'.",
            details={"error": type(exc).__name__},
        ) from exc


def _emit_app_error(exc: AppError) -> None:
    typer.echo(
        json.dumps(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
            ensure_ascii=False,
        ),
        err=True,
    )
    raise typer.Exit(code=1)


@app.command("download-models")
def download_models(
    eval_models: Annotated[
        bool,
        typer.Option("--eval", help="Also download pinned sentiment evaluation comparators."),
    ] = False,
    with_siebert: Annotated[
        bool,
        typer.Option(
            "--with-siebert",
            help="With --eval, also download the large binary SiEBERT reference model.",
        ),
    ] = False,
) -> None:
    """Download pinned offline inference and optional evaluation artefacts."""

    settings = get_settings()
    try:
        sentiment_path = download_sentiment_model(settings.models_dir)
        embedding_path = download_embedding_model(settings.models_dir)
        result: dict[str, str] = {
            "sentiment_model": str(sentiment_path),
            "embedding_model": str(embedding_path),
        }
        if with_siebert and not eval_models:
            raise ValueError("--with-siebert requires --eval")
        if eval_models:
            tabularis_path = download_evaluation_model(
                settings.models_dir,
                model_id=TABULARIS_MODEL_ID,
                revision=TABULARIS_MODEL_REVISION,
                dirname=TABULARIS_MODEL_DIRNAME,
            )
            result["evaluation_tabularis"] = str(tabularis_path)
            if with_siebert:
                siebert_path = download_evaluation_model(
                    settings.models_dir,
                    model_id=SIEBERT_MODEL_ID,
                    revision=SIEBERT_MODEL_REVISION,
                    dirname=SIEBERT_MODEL_DIRNAME,
                )
                result["evaluation_siebert"] = str(siebert_path)
        typer.echo(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        _emit_app_error(
            AppError(
                status_code=503,
                code="MODEL_UNAVAILABLE",
                message="Model download failed.",
                details={"error": type(exc).__name__},
            )
        )


@app.command()
def collect(
    app_value: Annotated[str, typer.Option("--app", help="Numeric App Store id or Apple URL.")],
    country: Annotated[
        str, typer.Option("--country", help="Verified storefront: us or gb.")
    ] = "us",
    n: Annotated[int, typer.Option("--n", min=1, max=200, help="Number of reviews.")] = 100,
    seed: Annotated[int | None, typer.Option("--seed", help="Seed in [0, 2**53).")] = None,
    provider: Annotated[
        str | None, typer.Option("--provider", help="itunes, fixture, or rss.")
    ] = None,
    window_days: Annotated[
        int | None,
        typer.Option(
            "--window-days",
            min=1,
            max=MAX_WINDOW_DAYS,
            help="Sample only reviews from the last N days (iTunes).",
        ),
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="JSON output path.")] = None,
    record_snapshot: Annotated[
        Path | None,
        typer.Option("--record-snapshot", help="Also record a replayable snapshot."),
    ] = None,
    excel: Annotated[bool, typer.Option("--excel", help="Add a UTF-8 BOM to CSV.")] = False,
) -> None:
    """Collect a reproducible random review sample and write JSON plus CSV."""

    settings = get_settings()
    selected_provider = provider or settings.default_provider
    instance: ITunesProvider | FixtureProvider | RSSProvider | None = None
    try:
        app_id = parse_app_id(app_value)
        storefront = normalise_country(country)
        sample_size = validate_sample_size(n)
        effective_seed = resolve_seed(seed)
        if window_days is not None and selected_provider != "itunes":
            raise AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="--window-days is supported only with --provider itunes.",
            )
        out_path = out or Path("reviews.json")
        if out_path.suffix.lower() == ".csv":
            raise AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="--out is the JSON path; the CSV is written next to it with a .csv suffix.",
                details={"out": str(out_path)},
            )
        instance = _provider(selected_provider, settings)
        if isinstance(instance, ITunesProvider):
            result = instance.sample(
                app_id,
                storefront,
                sample_size,
                effective_seed,
                window_days=window_days,
            )
        else:
            result = instance.sample(app_id, storefront, sample_size, effective_seed)
        write_collection_json(result, out_path)
        write_reviews_csv(result.reviews, out_path.with_suffix(".csv"), excel=excel)
        if record_snapshot is not None:
            write_collection_json(result, record_snapshot)
        typer.echo(
            json.dumps(
                {
                    "json": str(out_path),
                    "csv": str(out_path.with_suffix(".csv")),
                    "actual": result.sampling.actual,
                    "seed": result.sampling.seed,
                },
                ensure_ascii=False,
            )
        )
    except AppError as exc:
        _emit_app_error(exc)
    except OSError as exc:
        _emit_app_error(
            AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="Output could not be written.",
                details={"error": type(exc).__name__},
            )
        )
    finally:
        if isinstance(instance, (ITunesProvider, RSSProvider)):
            instance.close()


@app.command()
def walk(
    app_value: Annotated[str, typer.Option("--app", help="Numeric App Store id or Apple URL.")],
    country: Annotated[
        str, typer.Option("--country", help="Verified storefront: us or gb.")
    ] = "us",
) -> None:
    """Walk the reachable written-review population for one-off validation."""

    settings = get_settings()
    provider: ITunesProvider | None = None
    try:
        app_id = parse_app_id(app_value)
        storefront = normalise_country(country)
        provider = ITunesProvider(settings)
        app_info = provider.app_info(app_id, storefront)
        population = provider.population(app_id, storefront)
        reviews: list[Review] = []
        for start in range(0, population.reachable, 1000):
            end = min(start + 1000, population.reachable)
            batch, _requests, _retries = provider.fetch_window(app_id, storefront, start, end)
            reviews.extend(batch)

        raw_path = Path("data/raw") / f"population_{storefront}.jsonl"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_path.open("w", encoding="utf-8", newline="\n") as handle:
            for review in reviews:
                handle.write(json.dumps(review.model_dump(mode="json"), ensure_ascii=False) + "\n")

        counts = Counter(review.rating for review in reviews)
        mean = sum(review.rating for review in reviews) / len(reviews) if reviews else None
        years = Counter(
            str(review.created_at.year) for review in reviews if review.created_at is not None
        )
        aggregate = {
            "app": app_info.model_dump(mode="json"),
            "country": storefront,
            "population_total": population.total_written_reviews,
            "reachable": population.reachable,
            "walked": len(reviews),
            "written_review_mean": mean,
            "written_review_star_distribution": {
                str(star): counts.get(star, 0) for star in range(1, 6)
            },
            "reviews_per_year": dict(sorted(years.items())),
        }
        report_path = Path("reports") / f"population_{storefront}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(aggregate, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        typer.echo(json.dumps({"raw": str(raw_path), "aggregate": str(report_path)}))
    except AppError as exc:
        _emit_app_error(exc)
    finally:
        if provider is not None:
            provider.close()


@app.command()
def analyze(
    provider: Annotated[str, typer.Option("--provider", help="Collection provider.")] = "fixture",
    snapshot: Annotated[
        Path | None, typer.Option("--snapshot", help="Recorded snapshot JSON.")
    ] = None,
    distance_threshold: Annotated[
        float | None,
        typer.Option(
            "--distance-threshold",
            min=0.000001,
            max=2.0,
            help="Override the complaint-theme cosine distance threshold.",
        ),
    ] = None,
    out: Annotated[Path, typer.Option("--out", help="Analysis JSON output path.")] = Path(
        "analysis.json"
    ),
) -> None:
    """Analyse a recorded snapshot through the same core pipeline used by the API."""

    settings = get_settings()
    repository: StorageRepository | None = None
    try:
        if provider != "fixture" or snapshot is None:
            raise AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="analyze requires --provider fixture and --snapshot PATH.",
            )
        with snapshot.open("r", encoding="utf-8") as handle:
            collection = CollectionResult.model_validate(json.load(handle))
        sentiment = _runtime_sentiment(settings)
        embedder = _runtime_embedder(settings)
        repository = StorageRepository(settings.database_url)
        repository.create_schema()
        analysis, _rows = analyse_collection(
            collection,
            request_payload={
                "app": str(collection.app.app_id),
                "country": collection.app.country,
                "sample_size": collection.sampling.requested,
                "seed": collection.sampling.seed,
                "provider": "fixture",
                **(
                    {"window_days": collection.sampling.window_days}
                    if collection.sampling.window_days is not None
                    else {}
                ),
                "analyze": True,
                "distance_threshold": (
                    distance_threshold
                    if distance_threshold is not None
                    else settings.theme_distance_threshold
                ),
            },
            sentiment=sentiment,
            embedder=embedder,
            analyze=True,
            request_deadline_s=settings.request_deadline_s,
            theme_distance_threshold=(
                distance_threshold
                if distance_threshold is not None
                else settings.theme_distance_threshold
            ),
            unit_min_negative_score_positive_reviews=(
                settings.unit_min_negative_score_positive_reviews
            ),
            unit_min_negative_score_other=settings.unit_min_negative_score_other,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(analysis.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        typer.echo(
            json.dumps(
                {
                    "analysis": str(out),
                    "analysis_id": analysis.analysis_id,
                    "theme_distance_threshold": analysis.themes.get("distance_threshold"),
                    "theme_diagnostics": analysis.themes.get("diagnostics"),
                },
                ensure_ascii=False,
            )
        )
    except (AppError, OSError, ValueError) as exc:
        if isinstance(exc, AppError):
            _emit_app_error(exc)
        _emit_app_error(
            AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="Snapshot could not be read.",
                details={"error": type(exc).__name__},
            )
        )
    finally:
        if repository is not None:
            repository.close()


@app.command()
def report(
    analysis: Annotated[
        Path,
        typer.Option("--analysis", help="Committed analysis JSON."),
    ] = Path("reports/nebula_us_seed42.analysis.json"),
    population: Annotated[
        Path,
        typer.Option("--population", help="Population-walk aggregate JSON."),
    ] = Path("reports/population_us.json"),
    evaluation: Annotated[
        Path,
        typer.Option("--evaluation", help="Optional evaluation results JSON."),
    ] = Path("evaluation/results.json"),
    out: Annotated[
        Path,
        typer.Option("--out", help="Markdown report output path."),
    ] = Path("reports/nebula_us_seed42.md"),
    charts_dir: Annotated[
        Path,
        typer.Option("--charts-dir", help="Directory for report PNG charts."),
    ] = Path("reports/charts"),
) -> None:
    """Render the reproducible Markdown report and its PNG charts."""

    try:
        report_path, chart_paths = write_report_files(
            analysis_path=analysis,
            population_path=population,
            evaluation_path=evaluation,
            output_path=out,
            charts_dir=charts_dir,
        )
        typer.echo(
            json.dumps(
                {
                    "report": str(report_path),
                    "charts": {key: str(value) for key, value in chart_paths.items()},
                },
                ensure_ascii=False,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _emit_app_error(
            AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="Report inputs could not be read or rendered.",
                details={"error": type(exc).__name__},
            )
        )


@app.command("audit-categories")
def audit_categories(
    analysis: Annotated[
        Path,
        typer.Option("--analysis", help="Analysis JSON containing issue-category matches."),
    ] = Path("reports/nebula_us_seed42.analysis.json"),
    out: Annotated[
        Path,
        typer.Option("--out", help="Human category-audit CSV to create."),
    ] = Path("evaluation/category_audit_sheet.csv"),
) -> None:
    """Create a human precision-audit sheet from deterministic category matches."""

    try:
        if out.exists():
            raise ValueError(f"refusing to overwrite existing audit sheet: {out}")
        payload = json.loads(analysis.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("analysis JSON must contain an object")
        insights = payload.get("insights")
        if not isinstance(insights, dict):
            raise ValueError("analysis JSON does not contain insights")
        issue_categories = insights.get("issue_categories")
        if not isinstance(issue_categories, dict):
            raise ValueError("analysis JSON does not contain issue categories")
        raw_rows = issue_categories.get("audit_rows")
        if not isinstance(raw_rows, list) or not raw_rows:
            raise ValueError("analysis contains no supported category matches to audit")

        fields = ["category", "review_id", "matched_phrase", "sentence", "human_label"]
        rows: list[dict[str, str]] = []
        for index, raw in enumerate(raw_rows, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"invalid audit row {index}")
            row = {field: str(raw.get(field, "")) for field in fields[:-1]}
            if any(not row[field].strip() for field in fields[:-1]):
                raise ValueError(f"incomplete audit row {index}")
            row["human_label"] = ""
            rows.append(row)

        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        typer.echo(json.dumps({"audit_sheet": str(out), "rows": len(rows)}, ensure_ascii=False))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _emit_app_error(
            AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="Category audit sheet could not be created.",
                details={"error": str(exc)},
            )
        )


@app.command("tune-threshold")
def tune_threshold(
    pairs: Annotated[
        Path,
        typer.Option(
            "--pairs",
            help="CSV with distance and human_label columns from hand-labelled complaint pairs.",
        ),
    ],
) -> None:
    """Select the largest evaluated threshold with same-issue precision of at least 0.8."""

    try:
        labelled_pairs: list[tuple[float, bool]] = []
        with pairs.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = set(reader.fieldnames or [])
            if "distance" not in fieldnames or not ({"human_label", "same_issue"} & fieldnames):
                raise ValueError("pairs CSV must contain distance plus human_label or same_issue")
            label_field = "human_label" if "human_label" in fieldnames else "same_issue"
            for row_number, row in enumerate(reader, start=2):
                distance = float(str(row.get("distance", "")).strip())
                if not 0.0 <= distance <= 2.0:
                    raise ValueError(f"distance out of range on row {row_number}")
                same_issue = _parse_same_issue(str(row.get(label_field, "")), row_number)
                labelled_pairs.append((distance, same_issue))
        if not labelled_pairs:
            raise ValueError("pairs CSV contains no labelled pairs")
        result = select_distance_threshold(labelled_pairs)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, csv.Error) as exc:
        _emit_app_error(
            AppError(
                status_code=422,
                code="INVALID_INPUT",
                message="Threshold-pair labels could not be read.",
                details={"error": str(exc)},
            )
        )


def _parse_same_issue(value: str, row_number: int) -> bool:
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes", "same", "same_issue"}:
        return True
    if normalised in {"0", "false", "no", "different", "different_issue"}:
        return False
    raise ValueError(f"invalid same_issue value on row {row_number}: {value!r}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, min=1, max=65535, help="Bind port."),
) -> None:
    """Run the API server locally."""

    uvicorn.run("appstore_review_analysis.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
