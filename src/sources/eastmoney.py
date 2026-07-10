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


def is_plain_market_move(title: str, content: str = "") -> bool:
    text = f"{title}\n{content}"
    if not any(keyword.lower() in text.lower() for keyword in MARKET_NOISE_KEYWORDS):
        return False
    return not any(keyword.lower() in text.lower() for keyword in EXPLANATION_KEYWORDS)
