from __future__ import annotations

import os
import smtplib
import logging
from email.message import EmailMessage

from .models import DailyDigest, NewsItem

logger = logging.getLogger(__name__)

INDUSTRY_ORDER = [
    "AI Capex / 算力基础设施",
    "半导体与硬件供应链",
    "数据中心与电力",
    "AI 模型公司与商业化",
    "AI 应用与软件",
    "机器人 / 具身智能",
]

VAGUE_REPLACEMENTS = {
    "值得关注": "后续需要跟踪",
    "具有重要意义": "影响相关公司收入、成本或估值假设",
    "推动行业发展": "改变产业链供需或商业化节奏",
    "前景广阔": "后续兑现仍取决于订单、收入和成本变量",
    "持续赋能": "影响客户采用率和收入兑现节奏",
    "重要意义": "投研含义",
}


def render_email_text(digest: DailyDigest) -> str:
    lines: list[str] = [digest.subject, "", "抓取概览"]
    overview = _overview_lines(digest.opening_summary)
    lines.extend(overview)

    if not digest.items:
        lines.extend(
            [
                "",
                "一、今日核心信号 Top 5",
                "指定来源暂未抓取到可用于生成日报的新内容。",
                "",
                "二、产业链层次",
                "暂无可展开的产业链信号。",
                "",
                "三、公司层次",
                "暂无公司映射。",
                "",
                "四、政策 / 监管 / 出口管制",
                "暂无单独政策或监管信号。",
                "",
                "五、观察池",
                "暂无进入观察池的弱信号。",
            ]
        )
        return "\n".join(lines)

    lines.extend(["", "一、今日核心信号 Top 5"])
    for item in digest.items[:5]:
        lines.extend(
            [
                "",
                f"- 【{item.importance}】{_clean(item.title)}",
                f"  标签：{_industry_layer(item)}｜{_companies(item)}｜{item.signal_type or '产业链信号'}",
                f"  判断：{_brief(item.important_meaning, 90)}",
            ]
        )

    lines.extend(["", "二、产业链层次"])
    industry_grouped: dict[str, list[NewsItem]] = {layer: [] for layer in INDUSTRY_ORDER}
    extra_layers: dict[str, list[NewsItem]] = {}
    for item in digest.items:
        layer = _industry_layer(item)
        if layer == "政策 / 监管 / 出口管制":
            continue
        if layer in industry_grouped:
            industry_grouped[layer].append(item)
        else:
            extra_layers.setdefault(layer, []).append(item)

    rendered_industry = False
    for layer in [*INDUSTRY_ORDER, *extra_layers.keys()]:
        items = industry_grouped.get(layer) or extra_layers.get(layer, [])
        if not items:
            continue
        rendered_industry = True
        lines.extend(["", layer])
        for item in items:
            lines.extend(
                [
                    "",
                    f"【{item.importance}】{_clean(item.title)}",
                    _meta_line(item),
                    f"核心事实：{_brief(item.core_fact, 120)}",
                    f"投研含义：{_brief(item.important_meaning, 140)}",
                    f"传导链条：{_clean(item.transmission_chain or '该事件会传导至相关公司的收入、成本和估值假设。')}",
                    f"相关公司：{_companies(item)}",
                    f"后续跟踪变量：{_watch_variables(item)}",
                    f"链接：{item.url}",
                ]
            )
    if not rendered_industry:
        lines.extend(["", "暂无可展开的产业链信号。"])

    lines.extend(["", "三、公司层次"])
    company_rows = _company_rows(digest.items)
    if company_rows:
        lines.append("公司名称｜产业链位置｜相关事件｜投研影响｜后续跟踪变量")
        lines.extend(company_rows)
    else:
        lines.append("暂无公司映射。")

    lines.extend(["", "四、政策 / 监管 / 出口管制"])
    policy_items = [item for item in digest.items if _industry_layer(item) == "政策 / 监管 / 出口管制"]
    if policy_items:
        lines.append("事件｜涉及地区 / 监管主体｜影响环节｜相关公司｜投研影响｜后续变量")
        for item in policy_items:
            lines.append(
                "｜".join(
                    [
                        _clean(item.title),
                        "待确认",
                        _industry_layer(item),
                        _companies(item),
                        _brief(item.important_meaning, 80),
                        _watch_variables(item),
                    ]
                )
            )
    else:
        lines.append("暂无单独政策或监管信号。")

    lines.extend(["", "五、观察池"])
    selected_urls = {item.url for item in digest.items}
    watchlist = [item for item in digest.watchlist if item.url not in selected_urls]
    if watchlist:
        lines.append("标题｜层次｜相关公司｜信号类型｜分数｜状态｜后续观察点｜链接")
        for item in watchlist[:10]:
            lines.append(
                "｜".join(
                    [
                        _clean(item.title),
                        item.industry_layer,
                        "、".join(item.company_layer) if item.company_layer else "-",
                        item.signal_type,
                        str(item.score),
                        item.status,
                        "、".join(item.watch_variables[:3]) if item.watch_variables else "-",
                        item.url,
                    ]
                )
            )
    else:
        lines.append("暂无进入观察池的弱信号。")
    return "\n".join(lines).strip()


def _overview_lines(opening_summary: str) -> list[str]:
    bullets = [line.strip().lstrip("- ").strip() for line in opening_summary.splitlines() if line.strip()]
    range_line = next((line for line in bullets if "本期抓取范围" in line), "本期抓取范围：北京时间过去 24 小时。")
    count_line = next(
        (line for line in bullets if "今日共抓取候选链接" in line),
        "今日共抓取候选链接 0 条，成功提取正文 0 篇，通过投研过滤保留 0 篇，最终精选 0 条进入日报。",
    )
    return [f"- {_clean(range_line)}", f"- {_clean(count_line)}"]


def _industry_layer(item: NewsItem) -> str:
    if item.industry_layer:
        return item.industry_layer
    topic_map = {
        "AI Capex / 数据中心": "AI Capex / 算力基础设施",
        "算力与半导体供应链": "半导体与硬件供应链",
        "AI 公司与商业化": "AI 模型公司与商业化",
        "二级市场相关": "二级市场与资金面",
    }
    return topic_map.get(item.topic, "AI 应用与软件")


def _companies(item: NewsItem) -> str:
    return "、".join(item.company_layer[:6]) if item.company_layer else "-"


def _watch_variables(item: NewsItem) -> str:
    return "、".join(item.watch_variables[:4]) if item.watch_variables else "订单变化、收入兑现、成本变化"


def _meta_line(item: NewsItem) -> str:
    published = item.published_at or item.time_status or "时间不明"
    if "T" in published:
        published = published.replace("T", " ").split("+")[0].split(".")[0]
    discovery = item.discovery_method or "-"
    status = item.content_status or ("partial article" if item.is_partial else "正文可用")
    return f"{item.source}｜{published}｜{discovery}｜{status}"


def _company_rows(items: list[NewsItem]) -> list[str]:
    rows: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        for company in item.company_layer[:6]:
            key = (company, item.title)
            if key in seen:
                continue
            seen.add(key)
            impacts = "、".join(item.company_impact_type[:3]) if item.company_impact_type else "收入、成本或估值假设"
            rows.append(
                "｜".join(
                    [
                        company,
                        _industry_layer(item),
                        _clean(item.title),
                        f"影响{impacts}",
                        _watch_variables(item),
                    ]
                )
            )
    return rows[:15]


def _brief(text: str, max_chars: int) -> str:
    text = _clean(text)
    if len(text) <= max_chars:
        return text
    for mark in ("。", "；", "，", ";", ","):
        cut = text.rfind(mark, 0, max_chars + 1)
        if cut >= max_chars // 2:
            return text[: cut + 1].strip()
    return text[:max_chars].strip()


def _clean(text: str) -> str:
    cleaned = text.strip()
    for old, new in VAGUE_REPLACEMENTS.items():
        cleaned = cleaned.replace(old, new)
    return cleaned.replace("...", "").replace("…", "").strip()


def send_email(subject: str, body: str) -> None:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing email environment variables: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["EMAIL_FROM"]
    message["To"] = os.environ["EMAIL_TO"]
    message.set_content(body, subtype="plain", charset="utf-8")

    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
        elif port == 587:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(message)
        logger.info("Email sent successfully")
    except smtplib.SMTPException as exc:
        logger.error("SMTP error while sending email: %s", exc)
        raise
    except OSError as exc:
        logger.error("SMTP connection error while sending email: %s", exc)
        raise
