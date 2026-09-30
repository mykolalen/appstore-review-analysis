import csv
from datetime import UTC, datetime

from appstore_review_analysis.domain import Review
from appstore_review_analysis.export import write_reviews_csv


def _review(title: str, body: str) -> Review:
    return Review(
        source_review_id="1",
        rank=0,
        country="us",
        title=title,
        body=body,
        rating=1,
        created_at=datetime(2026, 9, 28, tzinfo=UTC),
        is_edited=False,
        vote_count=0,
        vote_sum=0,
        has_developer_response=False,
        developer_response_id=None,
        developer_response_date=None,
        provider="itunes",
    )


def test_csv_is_rfc4180_and_formula_prefixes_are_neutralised(tmp_path) -> None:
    dangerous = ['=HYPERLINK("x")', "+x", "-x", "@x", "\tx", "\rx", "\nx", "＝x"]
    path = tmp_path / "reviews.csv"
    write_reviews_csv([_review(dangerous[0], dangerous[1])], path)

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    with path.open("r", encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["title"].startswith("\t=")
    assert row["body"].startswith("\t+")

    for index, value in enumerate(dangerous):
        p = tmp_path / f"dangerous-{index}.csv"
        write_reviews_csv([_review(value, value)], p)
        with p.open("r", encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["title"].startswith("\t")
        assert row["body"].startswith("\t")


def test_excel_mode_adds_utf8_bom_only(tmp_path) -> None:
    path = tmp_path / "reviews.csv"
    write_reviews_csv([_review("safe", "safe")], path, excel=True)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
