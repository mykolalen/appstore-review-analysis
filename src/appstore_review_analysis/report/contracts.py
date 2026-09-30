"""Typed contract between analysis output and the report renderer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class IntervalContract(_ContractModel):
    low: float | None
    high: float | None
    reason: str | None = None


class AppContract(_ContractModel):
    app_id: int
    name: str
    country: str


class SamplingContract(_ContractModel):
    provider: str
    method: str
    storefront: str
    population_total: int
    reachable: int
    frame_description: str
    requested: int
    actual: int
    sampling_fraction: float
    seed: int
    collected_at: str


class DistributionRowContract(_ContractModel):
    count: int
    percentage: float | None
    ci95: IntervalContract


class StoreBenchmarkContract(_ContractModel):
    mean: float | None
    rating_count: int


class PeriodContract(_ContractModel):
    label: str
    n: int
    mean: float | None
    mean_ci95: IntervalContract
    one_two_star_share: float | None


class MetricsContract(_ContractModel):
    n: int
    mean: float | None
    mean_ci95: IntervalContract
    median: float | None
    stddev: float | None
    distribution: dict[str, DistributionRowContract]
    store_benchmark: StoreBenchmarkContract
    periods: list[PeriodContract]


class PreprocessingExample(_ContractModel):
    review_id: str
    before: str
    after: str
    flags: list[str]


class PreprocessingContract(_ContractModel):
    n_all: int
    n_analysable: int
    n_negative: int
    n_rest: int
    n_complaint_reviews: int
    n_units: int
    flag_counts: dict[str, int]
    language_counts: dict[str, int]
    examples: list[PreprocessingExample]


class SentimentConsistencyContract(_ContractModel):
    comparable_reviews: int
    agreement_rate: float | None
    note: str | None = None


class SentimentContract(_ContractModel):
    status: str
    distribution: dict[str, DistributionRowContract]
    rating_consistency: SentimentConsistencyContract
    model_id: str | None = None
    model_revision: str | None = None


class PhraseSupport(_ContractModel):
    count: int
    total: int
    display: str


class PhraseRow(_ContractModel):
    phrase: str
    support_count: int
    negative_reviews: PhraseSupport
    other_reviews: PhraseSupport
    z: float | None = None
    z_ge_1_96: bool | None = None


class KeywordsContract(_ContractModel):
    status: str
    n_negative: int
    n_rest: int
    common: list[PhraseRow]
    distinctive: list[PhraseRow]


class CoverageContract(_ContractModel):
    units_clustered: int
    units_total: int
    unit_share: float | None
    complaint_reviews_in_themes: int
    n_complaint_reviews: int
    review_share: float | None


class UnitGateContract(_ContractModel):
    positive_review_min_negative_score: float
    other_review_min_negative_score: float
    negative_label_candidates: int
    kept: int
    removed_total: int
    removed_by_rule: dict[str, int]


class ThemePhraseContract(_ContractModel):
    phrase: str
    support_count: int
    z: float


class ThemeRepresentativeContract(_ContractModel):
    review_id: str
    excerpt: str


class ThemeShareContract(_ContractModel):
    value: float | None
    numerator: int
    denominator: int
    ci95: IntervalContract


class ThemeContract(_ContractModel):
    theme_id: str
    unit_count: int
    review_count: int
    share_of_complaint_reviews: ThemeShareContract
    top_phrases: list[ThemePhraseContract]
    representative_units: list[ThemeRepresentativeContract]
    evidence_review_ids: list[str]


class ThemesContract(_ContractModel):
    status: str
    n_units: int
    n_complaint_reviews: int
    distance_threshold: float
    unit_gate: UnitGateContract
    coverage: CoverageContract
    diagnostics: dict[str, Any]
    items: list[ThemeContract]
    other: dict[str, Any]


class RecencyContract(_ContractModel):
    newest_date: str | None
    share_last_12_months: float | None
    historical: bool


class IssueCategoryEvidenceContract(_ContractModel):
    review_id: str
    excerpt: str


class IssueCategoryPhraseContract(_ContractModel):
    phrase: str
    count: int


class IssueCategoryItemContract(_ContractModel):
    category_id: str
    label: str
    description: str
    review_count: int
    denominator: int
    share: float | None
    ci95: IntervalContract
    mean_star_rating: float | None
    recency: RecencyContract
    top_matched_phrases: list[IssueCategoryPhraseContract]
    evidence: list[IssueCategoryEvidenceContract]
    matched_review_ids: list[str]
    suggested_investigation: str


class NotCategorisedContract(_ContractModel):
    review_count: int
    denominator: int
    share: float | None
    review_ids: list[str]
    evidence: list[IssueCategoryEvidenceContract]


class CategoryAuditRowContract(_ContractModel):
    category: str
    review_id: str
    matched_phrase: str
    sentence: str


class IssueCategoriesContract(_ContractModel):
    status: str
    version: str
    denominator: int
    multi_label: bool
    items: list[IssueCategoryItemContract]
    not_categorised: NotCategorisedContract
    audit_rows: list[CategoryAuditRowContract]


class ComplaintReviewSupportContract(_ContractModel):
    count: int
    total: int
    share: float | None
    ci95: IntervalContract | None


class InsightAreaContract(_ContractModel):
    source: Literal["issue_category", "theme", "phrase"]
    theme_id: str | None
    area: str
    complaint_reviews: ComplaintReviewSupportContract
    mean_star_rating: float | None
    recency: RecencyContract
    evidence_review_ids: list[str]
    text: str


class InsightsContract(_ContractModel):
    status: str
    issue_categories: IssueCategoriesContract | None = None
    areas_of_improvement: list[InsightAreaContract]


class ModelRefContract(_ContractModel):
    id: str
    revision: str


class ProvenanceContract(_ContractModel):
    sentiment_model: ModelRefContract | None
    embedding_model: ModelRefContract | None


class ReportAnalysisContract(_ContractModel):
    app: AppContract
    sampling: SamplingContract
    preprocessing: PreprocessingContract
    metrics: MetricsContract
    sentiment: SentimentContract
    keywords: KeywordsContract
    themes: ThemesContract
    insights: InsightsContract
    provenance: ProvenanceContract
    analysis_complete: bool
    warnings: list[str]
