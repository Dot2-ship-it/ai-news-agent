from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, HttpUrl


class Language(str, Enum):
    EN = "en"
    ZH = "zh"


class FetchStrategy(BaseModel):
    discovery_mode: Literal["direct", "list_page", "api", "rss", "gdelt_fallback", "premium_title_only"] = "direct"
    body_mode: Literal["required", "optional", "disabled"] = "required"
    allow_partial_article: bool = False
    fallback_discovery: list[Literal["gdelt", "mobile", "rss", "sec_api"]] = Field(default_factory=list)


class SourceConfig(BaseModel):
    id: str
    name: str
    source_name: str | None = None
    url: HttpUrl
    language: Language
    strategy: Literal["translate_and_summarize", "summarize"]
    source_type: Literal[
        "official_ir",
        "sec_filing",
        "market_news",
        "premium_business_news",
        "infra_media",
        "semiconductor_research",
        "chinese_flash",
        "flash_news",
        "chinese_industry",
    ] = "market_news"
    quality_tier: int = 3
    region: str = "global"
    requires_ai_filter: bool = False
    requires_investment_signal_filter: bool = False
    requires_time_filter: bool = False
    allow_unknown_time: bool = True
    crawl_depth: int = 1
    investment_relevance: Literal["high", "medium", "low"] = "medium"
    sec_ciks: dict[str, str] = Field(default_factory=dict)
    sec_forms: list[str] = Field(default_factory=lambda: ["8-K", "10-Q", "10-K"])
    fetch_strategy: FetchStrategy = Field(default_factory=FetchStrategy)
    alternate_urls: list[str] = Field(default_factory=list)
    gdelt_domains: list[str] = Field(default_factory=list)
    gdelt_query_terms: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    max_links: int = 20
    strict_ai_relevance: bool = False


class AgentConfig(BaseModel):
    tracked_companies: dict[str, list[str]] = Field(default_factory=dict)
    time_filter: dict[str, object] = Field(default_factory=dict)
    sources: list[SourceConfig]


class ArticleCandidate(BaseModel):
    source_id: str
    source_name: str
    source_language: Language
    source_strategy: str
    source_type: str = "market_news"
    quality_tier: int = 3
    region: str = "global"
    title: str | None = None
    url: str
    summary: str | None = None
    published_at: datetime | None = None
    body_status: str = "body_pending"
    discovery_status: str = "discovered"
    content_source: str = "article_body"
    discovery_method: str = "list_page"
    is_partial: bool = False
    partial_reason: str | None = None


class Article(BaseModel):
    source_id: str
    source_name: str
    source_language: Language
    source_strategy: str
    source_type: str = "market_news"
    quality_tier: int = 3
    region: str = "global"
    title: str
    url: str
    published_at: datetime | None = None
    time_status: Literal["published_within_window", "filtered_by_time_window", "time_unknown", "known", "unknown"] = "time_unknown"
    investment_score: int = 0
    matched_companies: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)
    topic: str = "核心信号"
    body_status: str = "body_available"
    discovery_status: str = "discovered"
    content_source: str = "article_body"
    discovery_method: str = "list_page"
    is_partial: bool = False
    partial_reason: str | None = None
    content: str


class NewsItem(BaseModel):
    importance: Literal["高", "中", "低"]
    topic: str = "核心信号"
    title: str
    source: str
    url: str
    core_fact: str = Field(validation_alias=AliasChoices("core_fact", "one_sentence", "summary", "核心事实"))
    key_points: list[str] = Field(default_factory=list, max_length=3)
    important_meaning: str = Field(validation_alias=AliasChoices("important_meaning", "why_it_matters", "重要意义"))
    content_status: str | None = None
    discovery_method: str | None = None
    published_at: str | None = None
    time_status: str | None = None
    investment_score: int = 0
    is_partial: bool = False
    industry_layer: str | None = None
    company_layer: list[str] = Field(default_factory=list)
    direct_companies: list[str] = Field(default_factory=list)
    inferred_companies: list[str] = Field(default_factory=list)
    watch_companies: list[str] = Field(default_factory=list)
    company_impact_type: list[str] = Field(default_factory=list)
    signal_type: str | None = None
    watch_variables: list[str] = Field(default_factory=list)
    transmission_chain: str | None = None


class WatchItem(BaseModel):
    title: str
    url: str
    source: str
    industry_layer: str
    company_layer: list[str] = Field(default_factory=list)
    direct_companies: list[str] = Field(default_factory=list)
    inferred_companies: list[str] = Field(default_factory=list)
    watch_companies: list[str] = Field(default_factory=list)
    signal_type: str
    score: int
    status: str
    watch_variables: list[str] = Field(default_factory=list)
    discovery_method: str | None = None


class DailyDigest(BaseModel):
    subject: str
    opening_summary: str
    trend: str
    items: list[NewsItem]
    watchlist: list[WatchItem] = Field(default_factory=list)


class SourceRunResult(BaseModel):
    source_id: str
    source_name: str
    source_type: str = "market_news"
    candidates: int = 0
    fetched: int = 0
    kept: int = 0
    partial_count: int = 0
    body_failed_count: int = 0
    discovery_failed_count: int = 0
    premium_limited_count: int = 0
    gdelt_fallback_count: int = 0
    gdelt_status: Literal["success", "skipped", "rate_limited", "failed"] = "skipped"
    gdelt_error_message: str | None = None
    discovery_methods: list[str] = Field(default_factory=list)
    list_page_only_count: int = 0
    filtered_by_time_count: int = 0
    filtered_by_relevance_count: int = 0
    filtered_by_url_count: int = 0
    failed_count: int = 0
    status: Literal[
        "success",
        "partial_success",
        "premium_limited",
        "body_unavailable",
        "fetch_failed",
        "no_recent_articles",
        "filtered_by_time_window",
        "no_investment_relevant_articles",
    ] = "no_recent_articles"
    filtered: list[str] = Field(default_factory=list)
    failed: bool = False
    error: str | None = None
    error_type: str | None = None
