# Defines Pydantic request, response,
# and LLM output schemas for link analysis,
# including synchronous URL format validation on LinkAnalysisRequest.

# Structures five analysis domains—competitor comparison,
# target search queries, SEO improvements, top services,
# and target audience—alongside shared enums and source/provenance tags.

# Models success payloads (LinkAnalysisResponse with AnalysisMeta)
# and error payloads (ErrorResponse with ErrorDetail)
# for API serialization and LLM response validation.

"""Pydantic request/response models."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import InvalidURLError, normalize_url, parse_and_check_format


# ---------- Request ----------
class LinkAnalysisRequest(BaseModel):
    url: str = Field(..., examples=["https://example.com"])

    @field_validator("url")
    @classmethod
    def check_url_format(cls, v: str) -> str:
        # Format only (sync). DNS/SSRF check happens later via validate_public_url.
        try:
            url = normalize_url(v)
            parse_and_check_format(url)
        except InvalidURLError as exc:
            raise ValueError(str(exc)) from exc
        return url


# ---------- Shared ----------
Source = Literal["website", "inferred"]


class Platform(str, Enum):
    GOOGLE = "Google"
    FACEBOOK = "Facebook"
    INSTAGRAM = "Instagram"
    LINKEDIN = "LinkedIn"
    X = "X"


class Brand(BaseModel):
    name: str
    category: str
    summary: str


# ---------- Section 1: Competitor vs brand ----------
class ComparisonProfile(BaseModel):
    brand_name: str
    main_service: str
    target_market: str
    key_strength: str
    opportunity: str


class Competitor(ComparisonProfile):
    website: str | None = None
    strengths: list[str] = []
    weaknesses: list[str] = []
    # Competitors are never verified here, so they are labelled as AI suggestions.
    source: Literal["ai_suggested", "website"] = "ai_suggested"


class CompetitorAnalysis(BaseModel):
    brand_overview: str
    business_category: str
    main_products_services: list[str]
    your_brand: ComparisonProfile
    competitors: list[Competitor]  # aim for 3+ when reliable info exists
    competitive_advantages: list[str]
    positioning_opportunities: list[str]


# ---------- Section 2: Search queries ----------
class SearchQuery(BaseModel):
    query: str
    platforms: list[Platform] = Field(..., min_length=1)
    intent: str
    business_relevance: str
    target_audience: str
    # No search volume / ranking fields on purpose: never invent them.


# ---------- Section 3: SEO ----------
class SupportingContent(BaseModel):
    type: Literal[
        "service_landing_page",
        "industry_page",
        "faq",
        "blog_topic",
        "location_page",
        "supporting_article",
    ]
    title: str
    rationale: str


class SeoImprovements(BaseModel):
    primary_keyword: str
    secondary_keywords: list[str]
    long_tail_keywords: list[str]
    business_keywords: list[str]
    recommended_title: str  # target <= 60 chars (enforced in prompt, B5)
    title_structure: str  # e.g. "Primary Keyword | Benefit | Brand"
    meta_description: str  # target <= 160 chars (enforced in prompt, B5)
    supporting_content: list[SupportingContent]


# ---------- Section 4: Services ----------
class Service(BaseModel):
    name: str
    description: str
    target_industry: str
    target_customer: str
    main_benefit: str
    source: Source  # "website" = found on page, "inferred" = AI recommendation


# ---------- Section 5: Target audience ----------
class AudienceSegment(BaseModel):
    audience: str
    business_type: str
    requirement: str


class TargetAudience(BaseModel):
    primary_audience: list[str]
    secondary_audience: list[str]
    industries: list[str]
    business_types: list[str]
    customer_profiles: list[str]
    geographic_focus: str | None = None
    customer_needs: list[str]
    customer_interests: list[str]
    recommended_segments: list[AudienceSegment]


# ---------- Analysis wrapper ----------
class Analysis(BaseModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery] = Field(..., min_length=5, max_length=5)
    seo_improvements: SeoImprovements
    top_services: list[Service] = Field(..., min_length=1, max_length=5)
    target_audience: TargetAudience


class LLMAnalysisResult(BaseModel):
    """Exactly what the LLM must return (used for validation in B4/B5)."""

    brand: Brand
    analysis: Analysis


# ---------- Response ----------
class AnalysisMeta(BaseModel):
    model_used: str
    cached: bool = False
    processing_ms: int


class LinkAnalysisResponse(BaseModel):
    success: Literal[True] = True
    website: str
    brand: Brand
    analysis: Analysis
    meta: AnalysisMeta | None = None


class ErrorDetail(BaseModel):
    code: str  # e.g. INVALID_URL, BLOCKED_URL, SITE_UNREACHABLE, LLM_ERROR
    message: str


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail
