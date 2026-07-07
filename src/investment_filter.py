from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import Article, SourceConfig

AI_KEYWORDS = (
    "AI",
    "artificial intelligence",
    "generative AI",
    "large language model",
    "LLM",
    "OpenAI",
    "Anthropic",
    "Claude",
    "Gemini",
    "DeepSeek",
    "Llama",
    "xAI",
    "大模型",
    "人工智能",
    "生成式AI",
    "生成式 AI",
    "智能体",
    "Agent",
    "算力",
    "机器人",
    "具身智能",
)

INVESTMENT_KEYWORDS = (
    "capex",
    "capital expenditure",
    "revenue",
    "margin",
    "gross margin",
    "guidance",
    "order",
    "backlog",
    "utilization",
    "customer",
    "contract",
    "supply chain",
    "data center",
    "datacenter",
    "hyperscale",
    "lease",
    "capacity",
    "MW",
    "GW",
    "GPU",
    "Blackwell",
    "GB200",
    "H100",
    "H200",
    "inference",
    "cloud",
    "Azure",
    "AWS",
    "Google Cloud",
    "Oracle Cloud",
    "power",
    "grid",
    "electricity",
    "cooling",
    "construction",
    "tariff",
    "nuclear",
    "funding",
    "valuation",
    "IPO",
    "acquisition",
    "M&A",
    "regulation",
    "export control",
    "antitrust",
    "资本开支",
    "收入",
    "毛利率",
    "业绩指引",
    "订单",
    "积压订单",
    "利用率",
    "客户",
    "合同",
    "供应链",
    "数据中心",
    "液冷",
    "AI芯片",
    "AI 芯片",
    "存储",
    "英伟达",
    "云服务",
    "甲骨文云",
    "电力",
    "电网",
    "冷却",
    "核电",
    "融资",
    "估值",
    "并购",
    "监管",
    "出口管制",
    "反垄断",
    "股价",
    "业绩",
    "指引",
)

NOISE_KEYWORDS = (
    "prompt",
    "tutorial",
    "how to",
    "guide",
    "benchmark",
    "leaderboard",
    "paper",
    "arxiv",
    "github",
    "open-source",
    "open source",
    "tool list",
    "工具合集",
    "教程",
    "提示词",
    "论文",
    "模型测评",
    "开源项目",
    "产品体验",
    "普通发布会",
    "活动宣传",
    "主题会",
    "邀请函",
    "活动",
    "峰会",
    "直播",
    "会议",
    "报名",
    "广告",
    "人物访谈",
)

SIGNAL_GROUPS = {
    "capex": ("capex", "capital expenditure", "资本开支"),
    "revenue_or_margin": ("revenue", "margin", "gross margin", "收入", "毛利率"),
    "guidance": ("guidance", "outlook", "forecast", "业绩指引", "指引"),
    "order_or_contract": ("order", "backlog", "contract", "customer", "订单", "积压订单", "合同", "客户"),
    "data_center_or_power": (
        "data center",
        "datacenter",
        "power",
        "grid",
        "electricity",
        "cooling",
        "nuclear",
        "hyperscale",
        "lease",
        "capacity",
        "MW",
        "GW",
        "construction",
        "tariff",
        "数据中心",
        "电力",
        "电网",
        "冷却",
        "液冷",
        "核电",
    ),
    "regulation_or_export_control": ("regulation", "export control", "antitrust", "监管", "出口管制", "反垄断"),
    "funding_or_valuation": ("funding", "valuation", "IPO", "acquisition", "M&A", "融资", "估值", "并购"),
    "market_reaction": ("shares", "stock", "market reaction", "analyst", "股价", "二级市场", "分析师"),
    "sec_filing": ("10-Q", "10-K", "8-K", "filing", "filed", "annual report", "quarterly report"),
}


@dataclass
class InvestmentDecision:
    keep: bool
    score: int
    ai_relevant: bool
    investment_signal_relevant: bool
    tracked_company_match: bool
    is_noise: bool
    matched_companies: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    topic: str = "核心信号"
    reason: str = ""


def flatten_tracked_companies(tracked_companies: dict[str, list[str]]) -> list[str]:
    companies: list[str] = []
    for names in tracked_companies.values():
        companies.extend(names)
    return sorted(set(companies), key=len, reverse=True)


def contains_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def matched_keywords(text: str, keywords: tuple[str, ...] | list[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword.lower() in lowered]


def matched_companies(text: str, tracked_companies: dict[str, list[str]]) -> list[str]:
    lowered = text.lower()
    matches = []
    for company in flatten_tracked_companies(tracked_companies):
        if company.lower() in lowered:
            matches.append(company)
    return matches


def signal_matches(text: str) -> list[str]:
    matches = []
    for signal, keywords in SIGNAL_GROUPS.items():
        if contains_any(text, keywords):
            matches.append(signal)
    return matches


def classify_topic(article: Article, signals: list[str]) -> str:
    text = f"{article.title}\n{article.content[:1600]}".lower()
    if any(signal in signals for signal in ("capex", "data_center_or_power")):
        return "AI Capex / 数据中心"
    if any(keyword in text for keyword in ("gpu", "blackwell", "gb200", "h100", "h200", "semiconductor", "芯片", "半导体", "英伟达")):
        return "算力与半导体供应链"
    if any(keyword in text for keyword in ("funding", "valuation", "customer", "revenue", "融资", "估值", "收入", "客户")):
        return "AI 公司与商业化"
    if any(signal in signals for signal in ("market_reaction", "guidance")):
        return "二级市场相关"
    if article.region in {"cn", "china"} or article.source_language.value == "zh":
        return "中国 AI 产业链"
    return "核心信号"


def score_article(
    article: Article,
    source: SourceConfig,
    tracked_companies: dict[str, list[str]],
    start_time: datetime,
    end_time: datetime,
) -> InvestmentDecision:
    text = f"{article.title}\n{article.content[:1800]}"
    companies = matched_companies(text, tracked_companies)
    signals = signal_matches(text)
    ai_relevant = contains_any(text, AI_KEYWORDS)
    investment_relevant = bool(signals) or contains_any(text, INVESTMENT_KEYWORDS)
    tracked_match = bool(companies)
    noise = contains_any(text, NOISE_KEYWORDS)

    score = 0
    if source.quality_tier == 1:
        score += 30
    elif source.quality_tier == 2:
        score += 20
    else:
        score += 10

    if source.source_type in {"official_ir", "sec_filing"}:
        score += 30
    if article.content_source == "sec_api":
        score += 15
    elif article.content_source == "official_ir_rss":
        score += 10
    elif article.content_source == "gdelt":
        score -= 10
    elif article.content_source == "list_page":
        score -= 5
    if article.is_partial:
        score -= 8

    if "capex" in signals:
        score += 25
    if "revenue_or_margin" in signals:
        score += 25
    if "guidance" in signals:
        score += 25
    if "order_or_contract" in signals:
        score += 20
    if "data_center_or_power" in signals:
        score += 20
    if "regulation_or_export_control" in signals:
        score += 15
    if "funding_or_valuation" in signals:
        score += 15
    if "market_reaction" in signals:
        score += 10
    if "sec_filing" in signals:
        score += 20
    if tracked_match:
        score += 20

    if article.published_at:
        hours_old = (end_time - article.published_at.astimezone(end_time.tzinfo)).total_seconds() / 3600
        if hours_old <= 24:
            score += 15
        elif hours_old <= 72:
            score += 5
    elif article.time_status in {"time_unknown", "unknown"}:
        score -= 15

    if noise:
        score -= 30
    if not investment_relevant:
        score -= 50

    keep = ((ai_relevant and investment_relevant) or (tracked_match and investment_relevant)) and not noise
    threshold = 45
    if source.source_type in {"official_ir", "sec_filing"} and tracked_match and investment_relevant:
        threshold = 40
    if source.source_type in {"chinese_industry", "flash_news"} and article.time_status in {"time_unknown", "unknown"}:
        threshold = 40
    if source.source_type == "flash_news":
        threshold = 55
    keep = keep and score >= threshold

    reason = "kept" if keep else "AI/投研相关性不足"
    if noise:
        reason = "噪声内容：教程/论文/测评/活动或工具类"
    elif not investment_relevant:
        reason = "缺少投研信号"
    elif not ai_relevant and not tracked_match:
        reason = "缺少 AI 相关性或重点公司匹配"
    elif score < threshold:
        reason = f"投研分数低于阈值：{score} < {threshold}"

    return InvestmentDecision(
        keep=keep,
        score=score,
        ai_relevant=ai_relevant,
        investment_signal_relevant=investment_relevant,
        tracked_company_match=tracked_match,
        is_noise=noise,
        matched_companies=companies[:8],
        matched_signals=signals,
        topic=classify_topic(article, signals),
        reason=reason,
    )


def is_url_noise(path: str, title: str | None = None) -> bool:
    noise_paths = (
        "/tag/",
        "/category/",
        "/topic/",
        "/events/",
        "/event/",
        "/about/",
        "/author/",
        "/newsletter/",
        "/search/",
        "/video/",
        "/podcast/",
    )
    if any(part in path for part in noise_paths):
        return True
    if title and re.search(r"(专题|活动|智库|首页|标签)", title):
        return True
    return False


def is_preferred_article_path(path: str) -> bool:
    preferred = (".html", "/news/", "/article/", "/articles/", "/detail/", "/press-release/", "/financial-reports/", "/filings/", "/p/")
    return any(part in path for part in preferred)
