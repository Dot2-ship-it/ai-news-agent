from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import Article, SourceConfig
from .sources.eastmoney import EASTMONEY_SOURCE_ID, has_investment_increment, is_plain_market_move

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

INDUSTRY_LAYER_KEYWORDS = {
    "政策 / 监管 / 出口管制": (
        "export control",
        "regulation",
        "antitrust",
        "出口管制",
        "监管",
        "反垄断",
        "数据安全",
    ),
    "半导体与硬件供应链": (
        "GPU",
        "Blackwell",
        "HBM",
        "DRAM",
        "PCB",
        "先进封装",
        "光模块",
        "晶圆",
        "EUV",
        "存储",
        "semiconductor",
        "accelerator",
        "chip",
    ),
    "数据中心与电力": (
        "data center",
        "datacenter",
        "MW",
        "GW",
        "power",
        "grid",
        "cooling",
        "liquid cooling",
        "液冷",
        "电力",
        "电网",
        "核电",
    ),
    "AI Capex / 算力基础设施": (
        "capex",
        "capital expenditure",
        "Azure",
        "AWS",
        "Oracle Cloud",
        "Meta infrastructure",
        "AI cloud",
        "hyperscale",
        "neocloud",
        "算力",
        "资本开支",
    ),
    "AI 模型公司与商业化": (
        "OpenAI",
        "Anthropic",
        "xAI",
        "DeepSeek",
        "API",
        "subscription",
        "enterprise customer",
        "商业化",
        "订阅",
        "企业客户",
    ),
    "机器人 / 具身智能": (
        "robot",
        "robotics",
        "具身智能",
        "Tesla Optimus",
        "Unitree",
        "UBTECH",
        "机器人",
    ),
    "二级市场与资金面": (
        "shares",
        "stock",
        "analyst",
        "valuation",
        "rating",
        "股价",
        "分析师",
        "估值",
        "评级",
        "资金",
        "去杠杆",
        "回调",
        "市场预期",
    ),
}

POLICY_ACTION_KEYWORDS = (
    "government",
    "regulator",
    "regulatory agency",
    "law",
    "sanction",
    "license",
    "export license",
    "export restriction",
    "export control",
    "compliance penalty",
    "regional restriction",
    "commerce department",
    "sec",
    "ftc",
    "doj",
    "eu commission",
    "政府",
    "监管机构",
    "法律",
    "制裁",
    "出口许可",
    "出口限制",
    "出口管制",
    "合规处罚",
    "地区限制",
    "商务部",
    "证监会",
    "反垄断",
    "数据安全",
)

DIRECT_COMPANY_ALIASES = {
    "SK Hynix": ("SK Hynix", "SK海力士", "海力士"),
    "NVIDIA": ("NVIDIA", "英伟达"),
    "AMD": ("AMD", "超威"),
    "Samsung": ("Samsung", "三星"),
    "Micron": ("Micron", "美光"),
    "Microsoft": ("Microsoft", "微软", "Azure"),
    "Amazon": ("Amazon", "亚马逊"),
    "AWS": ("AWS",),
    "Google": ("Google", "Alphabet", "谷歌"),
    "Meta": ("Meta",),
    "Oracle": ("Oracle", "甲骨文"),
    "CoreWeave": ("CoreWeave",),
    "Anthropic": ("Anthropic",),
    "OpenAI": ("OpenAI",),
}

INFERRED_COMPANIES_BY_LAYER = {
    "半导体与硬件供应链": ("Samsung", "Micron", "NVIDIA", "AMD"),
    "数据中心与电力": ("Vertiv", "Eaton", "Equinix", "Digital Realty"),
    "AI Capex / 算力基础设施": ("Microsoft", "Amazon", "Google", "Oracle", "Meta"),
}

COMPANY_IMPACT_KEYWORDS = {
    "收入": ("revenue", "sales", "收入", "营收"),
    "毛利率": ("margin", "gross margin", "毛利率"),
    "资本开支": ("capex", "capital expenditure", "资本开支"),
    "订单": ("order", "backlog", "contract", "订单", "积压订单", "合同"),
    "估值": ("valuation", "multiple", "估值"),
    "融资": ("funding", "financing", "IPO", "融资", "上市"),
    "供需": ("supply", "demand", "capacity", "utilization", "供给", "需求", "产能", "利用率"),
    "成本": ("cost", "price", "tariff", "成本", "价格", "关税"),
    "股价预期": ("shares", "stock", "analyst", "rating", "股价", "评级", "分析师"),
    "监管风险": ("regulation", "export control", "antitrust", "监管", "出口管制", "反垄断"),
}

WATCH_VARIABLES_BY_LAYER = {
    "AI Capex / 算力基础设施": ("云厂商 capex 指引", "AI 云租赁价格", "GPU 交付节奏", "算力利用率"),
    "半导体与硬件供应链": ("GPU 交付周期", "HBM 价格", "先进封装产能", "存储价格"),
    "数据中心与电力": ("MW/GW 签约容量", "电力接入进度", "液冷成本", "数据中心利用率"),
    "AI 模型公司与商业化": ("API 收入", "企业客户续约", "订阅转化率", "推理成本"),
    "AI 应用与软件": ("付费转化", "企业席位数", "客户留存", "单位推理成本"),
    "机器人 / 具身智能": ("量产节奏", "BOM 成本", "交付订单", "应用场景验证"),
    "政策 / 监管 / 出口管制": ("监管落地时间", "出口许可范围", "受限地区", "合规成本"),
    "二级市场与资金面": ("股价反应", "估值倍数", "资金流向", "分析师预期修正"),
}

TRANSMISSION_BY_LAYER = {
    "AI Capex / 算力基础设施": "云厂商资本开支变化会传导至 GPU 订单、AI 云供给和租赁价格假设。",
    "半导体与硬件供应链": "硬件供需变化会传导至 GPU 出货、HBM 定价、封装产能和相关供应商订单。",
    "数据中心与电力": "数据中心容量和电力约束会传导至 AI 云扩张节奏、租赁成本和基建 ROI 预期。",
    "AI 模型公司与商业化": "模型公司商业化进展会传导至 API 收入、企业订阅转化和推理成本假设。",
    "AI 应用与软件": "应用侧采用率变化会传导至软件收入、席位扩张和模型调用量。",
    "机器人 / 具身智能": "量产与订单验证会传导至硬件供应链需求、BOM 成本和收入兑现节奏。",
    "政策 / 监管 / 出口管制": "监管和出口限制会传导至供给可得性、合规成本和相关公司估值折价。",
    "二级市场与资金面": "资金面和预期变化会传导至估值倍数、股价弹性和市场风险偏好。",
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


def infer_industry_layer(text: str) -> str:
    title = text.split("\n", 1)[0]
    if contains_any(text, POLICY_ACTION_KEYWORDS):
        return "政策 / 监管 / 出口管制"
    if contains_any(text, ("HBM", "DRAM", "NAND", "ASIC", "PCB", "先进封装", "晶圆代工", "测试", "设备", "材料", "交付周期", "产能", "库存", "SK Hynix", "SK海力士")):
        return "半导体与硬件供应链"
    if contains_any(title, ("Blackwell", "芯片", "半导体", "NVIDIA", "AMD")):
        return "半导体与硬件供应链"
    if contains_any(text, INDUSTRY_LAYER_KEYWORDS["数据中心与电力"]):
        return "数据中心与电力"
    if contains_any(text, INDUSTRY_LAYER_KEYWORDS["AI Capex / 算力基础设施"]):
        return "AI Capex / 算力基础设施"
    if contains_any(text, INDUSTRY_LAYER_KEYWORDS["半导体与硬件供应链"]):
        return "半导体与硬件供应链"
    for layer in ("AI 模型公司与商业化", "机器人 / 具身智能", "二级市场与资金面"):
        if contains_any(text, INDUSTRY_LAYER_KEYWORDS[layer]):
            return layer
    if contains_any(text, AI_KEYWORDS):
        return "AI 模型公司与商业化"
    return "AI 应用与软件"


def infer_company_impact_type(text: str) -> list[str]:
    impacts = [impact for impact, keywords in COMPANY_IMPACT_KEYWORDS.items() if contains_any(text, keywords)]
    if impacts:
        return impacts[:4]
    return ["收入"] if contains_any(text, ("customer", "客户", "商业化")) else ["供需"]


def infer_signal_type(text: str, impacts: list[str], signals: list[str] | None = None) -> str:
    signals = signals or []
    if contains_any(text, ("HBM", "DRAM")) and contains_any(text, ("capex", "capacity", "扩产", "放缓", "产能", "资本开支")):
        return "存储供需 / HBM / DRAM / 资本开支"
    if "regulation_or_export_control" in signals or "监管风险" in impacts:
        return "监管 / 出口管制"
    if "capex" in signals or "资本开支" in impacts:
        return "资本开支"
    if "data_center_or_power" in signals:
        return "数据中心 / 电力约束"
    if "order_or_contract" in signals or "订单" in impacts:
        return "订单 / 客户"
    if "funding_or_valuation" in signals or any(impact in impacts for impact in ("融资", "估值")):
        return "融资 / 估值"
    if "market_reaction" in signals or "股价预期" in impacts:
        return "市场预期"
    if contains_any(text, ("API", "subscription", "enterprise customer", "商业化", "订阅")):
        return "商业化"
    return " / ".join(impacts[:2]) if impacts else "产业链信号"


def direct_company_matches(text: str, company_matches: list[str] | None = None) -> list[str]:
    direct: list[str] = []
    lowered = text.lower()
    for canonical, aliases in DIRECT_COMPANY_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            direct.append(canonical)
    for company in company_matches or []:
        if company not in direct and company.lower() in lowered:
            direct.append(company)
    return direct[:8]


def inferred_company_matches(layer: str, text: str, direct: list[str]) -> list[str]:
    inferred = [company for company in INFERRED_COMPANIES_BY_LAYER.get(layer, ()) if company not in direct]
    if layer == "半导体与硬件供应链" and contains_any(text, ("HBM", "DRAM", "NAND", "存储", "SK Hynix", "SK海力士")):
        return inferred[:4]
    if layer in {"数据中心与电力", "AI Capex / 算力基础设施"}:
        return inferred[:4]
    return []


def watch_company_matches(layer: str, direct: list[str], inferred: list[str]) -> list[str]:
    watched = []
    if layer == "AI 模型公司与商业化":
        watched = ["OpenAI", "Anthropic"]
    elif layer == "机器人 / 具身智能":
        watched = ["Tesla", "Unitree", "UBTECH"]
    return [company for company in watched if company not in direct and company not in inferred][:4]


def derive_report_fields(
    *,
    title: str,
    content: str,
    url: str = "",
    company_matches: list[str] | None = None,
    signal_matches_: list[str] | None = None,
    tracked_companies: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    text = f"{title}\n{content}\n{url}"
    companies = direct_company_matches(text, company_matches)
    if not companies and tracked_companies:
        companies = direct_company_matches(text, matched_companies(text, tracked_companies))
    signals = list(signal_matches_ or signal_matches(text))
    layer = infer_industry_layer(text)
    inferred_companies = inferred_company_matches(layer, text, companies)
    watch_companies = watch_company_matches(layer, companies, inferred_companies)
    impacts = infer_company_impact_type(text)
    variables = list(WATCH_VARIABLES_BY_LAYER.get(layer, ("收入兑现", "订单变化", "成本变化")))[:4]
    return {
        "industry_layer": layer,
        "company_layer": companies[:8],
        "direct_companies": companies[:8],
        "inferred_companies": inferred_companies,
        "watch_companies": watch_companies,
        "company_impact_type": impacts,
        "signal_type": infer_signal_type(text, impacts, signals),
        "watch_variables": variables,
        "transmission_chain": TRANSMISSION_BY_LAYER.get(layer, "该事件会传导至相关公司的收入、成本和估值假设。"),
    }


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
    eastmoney_increment = source.id == EASTMONEY_SOURCE_ID and has_investment_increment(article.title, article.content)
    eastmoney_plain_market_noise = source.id == EASTMONEY_SOURCE_ID and is_plain_market_move(article.title, article.content)
    if eastmoney_increment:
        investment_relevant = True
    tracked_match = bool(companies)
    noise = contains_any(text, NOISE_KEYWORDS) or eastmoney_plain_market_noise

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
    if eastmoney_increment:
        score += 15
    if eastmoney_plain_market_noise:
        score -= 40

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
    if source.id == EASTMONEY_SOURCE_ID:
        threshold = 55
    keep = keep and score >= threshold
    if source.id == EASTMONEY_SOURCE_ID and signals and not eastmoney_increment and set(signals).issubset({"market_reaction"}):
        keep = False

    reason = "kept" if keep else "AI/投研相关性不足"
    if noise:
        reason = "噪声内容：教程/论文/测评/活动、工具类或无明确原因的行情波动"
    elif source.id == EASTMONEY_SOURCE_ID and signals and not eastmoney_increment and set(signals).issubset({"market_reaction"}):
        reason = "东方财富行情异动缺少明确投资增量"
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
