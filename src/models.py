from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, HttpUrl


class Language(str, Enum):
    EN = "en"
    ZH = "zh"


class SourceConfig(BaseModel):
    id: str
    name: str
    url: HttpUrl
    language: Language
    strategy: Literal["translate_and_summarize", "summarize"]
    allowed_domains: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    max_links: int = 20


class AgentConfig(BaseModel):
    sources: list[SourceConfig]


class ArticleCandidate(BaseModel):
    source_id: str
    source_name: str
    source_language: Language
    source_strategy: str
    title: str | None = None
    url: str


class Article(BaseModel):
    source_id: str
    source_name: str
    source_language: Language
    source_strategy: str
    title: str
    url: str
    published_at: datetime | None = None
    content: str


class NewsItem(BaseModel):
    importance: Literal["高", "中", "低"]
    title: str
    source: str
    url: str
    core_fact: str = Field(validation_alias=AliasChoices("core_fact", "one_sentence", "summary", "核心事实"))
    key_points: list[str] = Field(default_factory=list, max_length=3)
    important_meaning: str = Field(validation_alias=AliasChoices("important_meaning", "why_it_matters", "重要意义"))


class DailyDigest(BaseModel):
    subject: str
    opening_summary: str
    trend: str
    items: list[NewsItem]


class SourceRunResult(BaseModel):
    source_id: str
    source_name: str
    candidates: int = 0
    fetched: int = 0
    kept: int = 0
    filtered: list[str] = Field(default_factory=list)
    failed: bool = False
    error: str | None = None
