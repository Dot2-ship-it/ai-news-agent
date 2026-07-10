from __future__ import annotations

EASTMONEY_SOURCE_ID = "eastmoney"
EASTMONEY_SOURCE_NAME = "东方财富"
EASTMONEY_SOURCE_TYPE = "market_news"

INVESTMENT_INCREMENT_KEYWORDS = (
    "公告",
    "业绩预告",
    "财报",
    "经营数据",
    "订单",
    "合同",
    "中标",
    "客户合作",
    "战略合作",
    "融资",
    "IPO",
    "上市",
    "并购",
    "收购",
    "资本开支",
    "capex",
    "产能",
    "扩产",
    "投产",
    "产业政策",
    "政策支持",
    "产业基金",
    "政府基金",
    "供应链",
    "供需",
    "价格",
    "估值",
    "收入",
    "利润",
    "毛利率",
)

AI_INVESTMENT_THEME_KEYWORDS = (
    "AI",
    "人工智能",
    "大模型",
    "算力",
    "GPU",
    "AI芯片",
    "AI 芯片",
    "半导体",
    "HBM",
    "DRAM",
    "存储芯片",
    "数据中心",
    "云计算",
    "光模块",
    "服务器",
    "液冷",
    "机器人",
    "具身智能",
    "OpenAI",
    "Anthropic",
    "DeepSeek",
    "NVIDIA",
    "英伟达",
    "AMD",
    "华为昇腾",
)

CONTEXTUAL_AI_KEYWORDS = (
    "芯片",
    "算力",
    "AI 基础设施",
    "AI基础设施",
    "服务器",
    "半导体",
)

EXCLUDED_NON_AI_TOPICS = (
    "航天",
    "火箭",
    "卫星",
    "军工",
    "低空经济",
    "可控核聚变",
    "新能源车",
    "光伏",
    "风电",
    "医药",
    "消费",
    "房地产",
    "纯行情复盘",
    "股吧",
    "互动易",
    "技术分析",
    "机构紧盯多只概念股",
)

MARKET_NOISE_KEYWORDS = (
    "涨幅",
    "跌幅",
    "拉升",
    "跳水",
    "走强",
    "走弱",
    "异动",
    "短线",
    "股吧",
    "评论",
    "互动问答",
    "技术分析",
    "K线",
    "换手率",
)

EXPLANATION_KEYWORDS = (
    "因",
    "由于",
    "受益",
    "带动",
    "订单",
    "合同",
    "公告",
    "政策",
    "业绩",
    "产能",
    "融资",
    "客户",
)


def has_investment_increment(title: str, content: str = "") -> bool:
    text = f"{title}\n{content}"
    return any(keyword.lower() in text.lower() for keyword in INVESTMENT_INCREMENT_KEYWORDS)


def has_ai_investment_theme(title: str, content: str = "") -> bool:
    text = f"{title}\n{content}"
    lowered = text.lower()
    has_direct_ai = any(keyword.lower() in lowered for keyword in AI_INVESTMENT_THEME_KEYWORDS)
    has_contextual_substitution = "国产替代" in text and any(keyword in text for keyword in CONTEXTUAL_AI_KEYWORDS)
    return has_direct_ai or has_contextual_substitution


def is_excluded_non_ai_topic(title: str, content: str = "") -> bool:
    text = f"{title}\n{content}"
    if not any(keyword in text for keyword in EXCLUDED_NON_AI_TOPICS):
        return False
    return not has_ai_investment_theme(title, content)


def is_plain_market_move(title: str, content: str = "") -> bool:
    text = f"{title}\n{content}"
    if not any(keyword.lower() in text.lower() for keyword in MARKET_NOISE_KEYWORDS):
        return False
    return not any(keyword.lower() in text.lower() for keyword in EXPLANATION_KEYWORDS)
