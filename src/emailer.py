from __future__ import annotations

import os
import smtplib
import logging
import re
from datetime import datetime
from email.message import EmailMessage
from html import escape

from .event_aggregator import DigestEvent, EventBundle, build_event_bundle, status_label
from .models import DailyDigest, NewsItem, WatchItem

logger = logging.getLogger(__name__)
EMAIL_BODY_MAX_CHARS = 12000
HTML_BODY_MAX_CHARS = 50000

INDUSTRY_ORDER = [
    "AI Capex / 算力基础设施",
    "半导体与硬件供应链",
    "数据中心与电力",
    "AI 模型公司与商业化",
    "机器人 / 具身智能",
    "政策 / 监管 / 出口管制",
]

OBSERVATION_BLOCKLIST = (
    "semianalysis",
    "core research",
    "data product",
    "data products",
    "semianalysis-data-products",
    "chipbook",
    "events",
    "semianalysis-events",
    "join exclusive tech events",
    "compliance policies",
    "compliance polices",
)

VAGUE_REPLACEMENTS = {
    "值得关注": "后续需要跟踪",
    "具有重要意义": "影响相关公司收入、成本或估值假设",
    "推动行业发展": "改变产业链供需或商业化节奏",
    "前景广阔": "后续兑现仍取决于订单、收入和成本变量",
    "持续赋能": "影响客户采用率和收入兑现节奏",
    "重要意义": "投研含义",
}


def render_email_subject(digest: DailyDigest, bundle: EventBundle | None = None) -> str:
    bundle = bundle or build_event_bundle(digest)
    base = digest.subject.split("｜")[0:2]
    base_subject = "｜".join(base) if len(base) == 2 else digest.subject
    keywords = []
    for event in bundle.core_events[:3]:
        keyword = _subject_keyword(event)
        if keyword not in keywords:
            keywords.append(keyword)
    return f"{base_subject}｜{'、'.join(keywords[:3])}" if keywords else base_subject


def render_email_text(digest: DailyDigest, source_stats: list[dict[str, object]] | None = None) -> str:
    bundle = build_event_bundle(digest)
    subject = render_email_subject(digest, bundle)
    lines: list[str] = [subject, "", "今日摘要"]
    lines.extend(_summary_lines(bundle))

    lines.extend(["", _core_section_title(len(bundle.core_events))])
    if not bundle.core_events:
        lines.extend(
            [
                "指定来源暂未抓取到可进入主日报的高置信新增内容。",
                "",
                "主线变化",
                "暂无可归纳的主线变化。",
                "",
                "观察池",
                *_render_watch_events(bundle.watch_events),
            ]
        )
        return _enforce_body_length("\n".join(lines))

    for event in bundle.core_events:
        lines.extend(_render_core_event(event, bundle))

    lines.extend(["", "主线变化"])
    lines.extend(_render_theme_changes(bundle))

    lines.extend(["", "观察池"])
    lines.extend(_render_watch_events(bundle.watch_events))
    return _enforce_body_length("\n".join(lines).strip())


def render_email_html(digest: DailyDigest, source_stats: list[dict[str, object]] | None = None) -> str:
    bundle = build_event_bundle(digest)
    subject = render_email_subject(digest, bundle)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(subject)}</title>
  <style>
    body {{ margin: 0; padding: 0; background-color: #F5F7FA; color: #1F2937; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; font-size: 14px; line-height: 1.75; }}
    .wrap {{ max-width: 680px; margin: 0 auto; padding: 24px 14px; }}
    .card {{ background-color: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 10px; padding: 18px; margin-bottom: 14px; }}
    .title {{ font-size: 22px; font-weight: 700; line-height: 1.35; color: #1F2937; margin: 0 0 8px; }}
    .section-title {{ font-size: 17px; font-weight: 700; color: #1E3A8A; margin: 0 0 12px; }}
    .event-title {{ font-size: 15px; font-weight: 700; color: #1F2937; margin: 0 0 8px; }}
    .muted {{ color: #6B7280; font-size: 12px; }}
    .metric-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .metric {{ border: 1px solid #E5E7EB; border-radius: 8px; padding: 10px; background-color: #F9FAFB; }}
    .metric-value {{ font-size: 20px; font-weight: 700; color: #1E3A8A; }}
    .metric-label {{ font-size: 12px; color: #6B7280; }}
    .tag {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background-color: #EFF6FF; color: #1E3A8A; font-size: 12px; margin-right: 6px; }}
    .tag-soft {{ background-color: #F3F4F6; color: #6B7280; }}
    .event-card {{ border: 1px solid #E5E7EB; border-radius: 10px; padding: 14px; margin-bottom: 12px; }}
    .label {{ font-weight: 700; color: #1F2937; }}
    a {{ color: #2563EB; text-decoration: none; }}
    ul {{ margin: 6px 0 0 20px; padding: 0; }}
    .compact-item {{ padding: 8px 0; border-top: 1px solid #E5E7EB; }}
    .compact-item:first-child {{ border-top: 0; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; border-bottom: 1px solid #E5E7EB; padding: 8px; vertical-align: top; }}
    th {{ color: #6B7280; font-size: 12px; font-weight: 700; }}
    td {{ font-size: 13px; }}
    .watch {{ background-color: #F9FAFB; font-size: 13px; }}
    .diagnostics {{ background-color: #F9FAFB; color: #6B7280; font-size: 12px; }}
    @media (max-width: 520px) {{ .metric-grid {{ grid-template-columns: 1fr; }} .wrap {{ padding: 12px; }} .card {{ padding: 14px; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1 class="title">{escape(subject)}</h1>
      <div class="muted">生成时间：北京时间 {escape(generated_at)}｜有效事件数：{len(bundle.core_events)}</div>
    </div>
    {_html_summary(bundle)}
    {_html_core_events(bundle)}
    {_html_theme_changes(bundle)}
    {_html_watchlist(bundle)}
    {_html_diagnostics(source_stats)}
  </div>
</body>
</html>"""
    return _enforce_html_length(html)


def _overview_lines(
    opening_summary: str,
    selected_count: int,
    source_stats: list[dict[str, object]] | None = None,
) -> list[str]:
    bullets = [line.strip().lstrip("- ").strip() for line in opening_summary.splitlines() if line.strip()]
    range_line = next((line for line in bullets if "本期抓取范围" in line), "本期抓取范围：北京时间过去 24 小时。")
    count_line = next(
        (line for line in bullets if "今日共抓取候选链接" in line),
        "今日共抓取候选链接 0 条，成功提取正文 0 篇，通过投研过滤保留 0 篇，最终精选 0 条进入日报。",
    )
    count_line = re.sub(r"最终精选\s*\d+\s*条", f"最终精选 {selected_count} 条", count_line)
    lines = [f"- {_clean(range_line)}", f"- {_clean(count_line)}"]
    if source_stats is not None:
        success = sum(1 for stat in source_stats if stat.get("status") == "success")
        partial = sum(1 for stat in source_stats if stat.get("status") == "partial_success")
        failed = sum(1 for stat in source_stats if stat.get("status") in {"fetch_failed", "body_unavailable"})
        lines.append(f"- 抓取健康度：成功源 {success} 个，部分成功源 {partial} 个，失败源 {failed} 个。")
    return lines


def _summary_lines(bundle: EventBundle) -> list[str]:
    strongest = bundle.theme_changes[0].theme_name if bundle.theme_changes else "暂无明确主线"
    variables = _dedup_variables(bundle.core_events)[:2]
    watch_count = len(bundle.watch_events)
    return [
        f"- 主线最强：{strongest}。",
        f"- 需要验证：{'、'.join(variables) if variables else '后续官方披露'}。",
        f"- 待验证信息：观察池 {watch_count} 条，低置信内容不进核心信号。",
    ]


def _html_summary(bundle: EventBundle) -> str:
    body = "".join(f"<div>{escape(line)}</div>" for line in _summary_lines(bundle))
    return _section_card("今日摘要", body)


def _html_core_events(bundle: EventBundle) -> str:
    title = _core_section_title(len(bundle.core_events))
    if not bundle.core_events:
        return _section_card(title, "<p>指定来源暂未抓取到可进入主日报的高置信新增内容。</p>")
    parts = []
    for event in bundle.core_events[:3]:
        variables = "".join(
            f"<li>{escape(variable.name)}：{escape(variable.direction_to_watch)}，{escape(variable.why)}</li>"
            for variable in event.follow_up_variables[:3]
        )
        parts.append(
            f"""
            <div class="event-card">
              <div><span class="tag">{escape(event.importance)}</span><span class="tag tag-soft">{escape(status_label(event.signal_status))}</span></div>
              <h3 class="event-title">{escape(_clean(event.title))}</h3>
              <div class="muted">{escape(event.industry_layer)}｜{escape(_theme_labels(event, bundle))}｜证据 {escape(event.evidence_level)}｜置信度 {escape(event.confidence_level)}</div>
              <p><span class="label">事实摘要：</span>{escape(_brief(event.fact, 120))}</p>
              <p><span class="label">增量判断：</span>{escape(_incremental_judgment(event))}</p>
              <p><span class="label">投研含义：</span>{escape(_brief(event.investment_implication, 140))}</p>
              <p><span class="label">影响公司：</span>{escape(_impact_companies(event))}</p>
              <div><span class="label">下一步验证：</span><ul>{variables}</ul></div>
              <p><a href="{escape(event.canonical_url, quote=True)}">查看原文</a></p>
            </div>
            """
        )
    return _section_card(title, "".join(parts))


def _html_theme_changes(bundle: EventBundle) -> str:
    if not bundle.theme_changes:
        return _section_card("主线变化", "<p>暂无可归纳的主线变化。</p>")
    items = []
    for change in bundle.theme_changes:
        events = _events_for_theme(bundle, change.theme_id)
        items.append(
            f"""
            <div class="compact-item">
              <div><span class="tag">{escape(status_label(change.signal_status))}</span><strong>{escape(change.theme_name)}</strong></div>
              <div class="muted">今日增量：新增 {change.evidence_count} 条证据｜证据质量：{escape(_theme_evidence_quality(change))}</div>
              <div>投资含义：{escape(_theme_implication(change.theme_name))}</div>
              <div>下一步验证：{escape('、'.join(_dedup_variables(events)[:3]) or '后续官方披露')}</div>
            </div>
            """
        )
    return _section_card("主线变化", "".join(items))


def _html_industry_layers(bundle: EventBundle) -> str:
    grouped: dict[str, list[DigestEvent]] = {layer: [] for layer in INDUSTRY_ORDER}
    for event in bundle.core_events:
        if event.industry_layer in grouped:
            grouped[event.industry_layer].append(event)
    parts = []
    for layer in INDUSTRY_ORDER:
        events = grouped.get(layer, [])[:3]
        if not events:
            continue
        rows = []
        for event in events:
            rows.append(
                f"""
                <div class="compact-item">
                  <strong>{escape(_clean(event.title))}</strong>
                  <div>{escape(_brief(event.investment_implication, 90))}</div>
                  <div><a href="{escape(event.canonical_url, quote=True)}">查看原文</a></div>
                </div>
                """
            )
        parts.append(f"<h3 class=\"event-title\">{escape(layer)}</h3>{''.join(rows)}")
    return _section_card("产业链层次", "".join(parts) if parts else "<p>暂无可展开的产业链信号。</p>")


def _html_company_table(bundle: EventBundle) -> str:
    rows = []
    seen: set[str] = set()
    for event in bundle.core_events:
        for company in event.direct_companies:
            if company in seen:
                continue
            seen.add(company)
            rows.append(
                f"""
                <tr>
                  <td>{escape(company)}</td>
                  <td>{escape(event.industry_layer)}</td>
                  <td>{escape(_clean(event.title))}</td>
                  <td>{escape(_format_variables(event))}</td>
                </tr>
                """
            )
            if len(seen) >= 8:
                break
    if not rows:
        return _section_card("公司层次", "<p>暂无公司映射。</p>")
    table = (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>公司</th><th>产业链位置</th><th>相关事件</th><th>后续变量</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    return _section_card("公司层次", table)


def _html_watchlist(bundle: EventBundle) -> str:
    if not bundle.watch_events:
        return _section_card("观察池", '<div class="watch">暂无进入观察池的弱信号。</div>')
    rows = []
    for event in bundle.watch_events[:8]:
        companies = _join(event.direct_companies)
        rows.append(
            f"""
            <div class="compact-item watch">
              <strong>{escape(_clean(event.title))}</strong>
              <div class="muted">{escape(event.source)}｜{escape(companies)}｜{escape(status_label(event.signal_status))}</div>
              <a href="{escape(event.canonical_url, quote=True)}">查看原文</a>
            </div>
            """
        )
    return _section_card("观察池", "".join(rows))


def _html_weekly_follow(bundle: EventBundle) -> str:
    if not bundle.follow_up_events:
        return _section_card("本周继续追踪", "<p>暂无明确追踪项。</p>")
    rows = []
    for idx, event in enumerate(bundle.follow_up_events[:5], start=1):
        rows.append(
            f"""
            <div class="compact-item">
              <strong>{idx}. {escape(_clean(event.title))}</strong>
              <div class="muted">当前状态：{escape(status_label(event.signal_status))}</div>
              <div>下一步看：{escape(_format_variables(event))}</div>
            </div>
            """
        )
    return _section_card("本周继续追踪", "".join(rows))


def _html_diagnostics(source_stats: list[dict[str, object]] | None) -> str:
    source_stats = source_stats or []
    success = [str(stat.get("source_name") or stat.get("source")) for stat in source_stats if stat.get("status") == "success"]
    partial = [
        str(stat.get("source_name") or stat.get("source"))
        for stat in source_stats
        if stat.get("status") == "partial_success"
    ]
    failed = [
        str(stat.get("source_name") or stat.get("source"))
        for stat in source_stats
        if stat.get("status") in {"fetch_failed", "body_unavailable"}
    ]
    body = (
        f"<div class=\"diagnostics\">"
        f"<div><strong>成功源：</strong>{escape(_join(success))}</div>"
        f"<div><strong>部分成功源：</strong>{escape(_join(partial))}</div>"
        f"<div><strong>失败源：</strong>{escape(_join(failed))}</div>"
        f"</div>"
    )
    return _section_card("抓取诊断", body)


def _section_card(title: str, body: str) -> str:
    return f'<div class="card"><h2 class="section-title">{escape(title)}</h2>{body}</div>'


def _metric_block(label: str, value: str) -> str:
    return (
        f'<div class="metric"><div class="metric-value">{escape(value)}</div>'
        f'<div class="metric-label">{escape(label)}</div></div>'
    )


def _strip_bullet(text: str) -> str:
    return text.strip().lstrip("- ").strip()


def _extract_metric(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else "-"


def _core_section_title(count: int) -> str:
    return "今日暂无高置信投研信号" if count == 0 else f"核心信号 Top {count}"


def _render_core_event(event: DigestEvent, bundle: EventBundle) -> list[str]:
    lines = [
        "",
        f"事件标题：{_clean(event.title)}",
        f"产业链标签：{event.industry_layer}",
        f"主线标签：{_theme_labels(event, bundle)}",
        f"事实摘要：{_brief(event.fact, 120)}",
        f"增量判断：{_incremental_judgment(event)}",
        f"投研含义：{_brief(event.investment_implication, 140)}",
        f"影响公司：{_impact_companies(event)}",
        "下一步验证：",
    ]
    for variable in event.follow_up_variables[:3]:
        lines.append(f"- {variable.name}：{variable.direction_to_watch}，{variable.why}")
    if event.confidence_level == "低":
        lines.append("备注：低置信度，待确认。")
    lines.append(f"链接：{event.canonical_url}")
    return lines


def _render_theme_changes(bundle: EventBundle) -> list[str]:
    if not bundle.theme_changes:
        return ["暂无可归纳的主线变化。"]
    lines = []
    for change in bundle.theme_changes:
        events = _events_for_theme(bundle, change.theme_id)
        lines.extend(
            [
                f"- 主线：{change.theme_name}",
                f"  状态：{status_label(change.signal_status)}",
                f"  今日增量：新增 {change.evidence_count} 条证据",
                f"  证据质量：{_theme_evidence_quality(change)}",
                f"  投资含义：{_theme_implication(change.theme_name)}",
                f"  下一步验证：{'、'.join(_dedup_variables(events)[:3]) or '后续官方披露'}",
            ]
        )
    return lines


def _company_event_cards(events: list[DigestEvent]) -> list[str]:
    cards: list[str] = []
    seen: set[str] = set()
    for event in events:
        for company in event.direct_companies:
            if company in seen:
                continue
            seen.add(company)
            cards.extend(
                [
                    "",
                    f"公司：{company}",
                    f"事件：{_clean(event.title)}",
                    f"影响方向：{event.signal_type}",
                    f"影响路径：{_clean(event.transmission_chain)}",
                    f"后续跟踪变量：{_format_variables(event)}",
                ]
            )
            if len(seen) >= 8:
                return cards
    return cards


def _render_watch_events(events: list[DigestEvent]) -> list[str]:
    if not events:
        return ["暂无进入观察池的弱信号。"]
    lines: list[str] = []
    for event in events[:8]:
        lines.extend(
            [
                "",
                f"事件：{_clean(event.title)}",
                f"层次：{event.industry_layer}",
                f"信号状态：{status_label(event.signal_status)}",
                f"证据等级：{event.evidence_level}",
                f"置信度：{event.confidence_level}",
                f"后续观察点：{_format_variables(event)}",
                f"链接：{event.canonical_url}",
            ]
        )
    return lines


def _render_weekly_follow_up(bundle: EventBundle) -> list[str]:
    if not bundle.follow_up_events:
        return ["暂无明确追踪项。"]
    lines: list[str] = []
    for idx, event in enumerate(bundle.follow_up_events[:5], start=1):
        lines.extend(
            [
                f"{idx}. {_clean(event.title)}",
                f"   - 当前状态：{status_label(event.signal_status)}",
                f"   - 下一步看：{_format_variables(event)}",
            ]
        )
    return lines


def _subject_keyword(event: DigestEvent) -> str:
    text = f"{event.title} {event.signal_type} {event.industry_layer}"
    if any(keyword in text for keyword in ("估值", "股价", "重定价", "回落")):
        return "AI估值"
    if any(keyword in text for keyword in ("HBM", "DRAM", "存储")):
        return "HBM供需"
    if any(keyword in text for keyword in ("国产", "推理芯片", "芯片替代")):
        return "国产芯片"
    if any(keyword in text for keyword in ("数据中心", "电力", "MW", "GW")):
        return "数据中心电力"
    if any(keyword in text for keyword in ("出口", "监管", "制裁")):
        return "出口管制"
    if any(keyword in text for keyword in ("capex", "资本开支", "ROI")):
        return "AI capex"
    return event.industry_layer.split(" / ")[0][:8]


def _format_variables(event: DigestEvent) -> str:
    return "、".join(variable.name for variable in event.follow_up_variables[:3]) or "后续官方披露"


def _theme_labels(event: DigestEvent, bundle: EventBundle) -> str:
    theme_names = []
    for theme_id in event.theme_ids:
        change = next((item for item in bundle.theme_changes if item.theme_id == theme_id), None)
        if change and change.theme_name not in theme_names:
            theme_names.append(change.theme_name)
    return "、".join(theme_names[:2]) if theme_names else "未归类主线"


def _incremental_judgment(event: DigestEvent) -> str:
    return f"{status_label(event.signal_status)}，证据等级 {event.evidence_level}，置信度 {event.confidence_level}。"


def _impact_companies(event: DigestEvent) -> str:
    direct = _join(event.direct_companies)
    peers = _join([company + "（间接传导）" for company in event.peer_companies])
    return f"直接：{direct}；间接：{peers}"


def _events_for_theme(bundle: EventBundle, theme_id: str) -> list[DigestEvent]:
    return [event for event in [*bundle.core_events, *bundle.watch_events] if theme_id in event.theme_ids]


def _dedup_variables(events: list[DigestEvent]) -> list[str]:
    variables: list[str] = []
    for event in events:
        for variable in event.follow_up_variables:
            if variable.name not in variables:
                variables.append(variable.name)
    return variables


def _theme_evidence_quality(change) -> str:
    if change.high_weight_count:
        return f"{change.high_weight_count} 条高权重证据"
    return "待交叉验证"


def _theme_implication(theme_name: str) -> str:
    if "HBM" in theme_name or "存储" in theme_name:
        return "影响存储价格、GPU 交付和硬件供应链利润弹性。"
    if "数据中心" in theme_name or "电力" in theme_name:
        return "影响 AI 云扩张节奏、租赁成本和基建 ROI 假设。"
    if "出口" in theme_name or "监管" in theme_name:
        return "影响销售区域、供给可得性和估值折价。"
    if "capex" in theme_name or "回报" in theme_name:
        return "影响云厂商资本开支持续性和 AI 基建估值。"
    if "估值" in theme_name:
        return "影响市场风险偏好和 AI 资产重定价。"
    return "影响相关公司的收入兑现、成本假设和估值预期。"


def _join(values: list[str]) -> str:
    return "、".join(values[:6]) if values else "-"


def _enforce_body_length(body: str) -> str:
    if len(body) <= EMAIL_BODY_MAX_CHARS:
        return body
    return body[: EMAIL_BODY_MAX_CHARS - 40].rstrip() + "\n\n[正文已按长度上限截断，详细链接请见原文。]"


def _enforce_html_length(body: str) -> str:
    if len(body) <= HTML_BODY_MAX_CHARS:
        return body
    return body[: HTML_BODY_MAX_CHARS - 80].rstrip() + "<p>正文已按长度上限截断，详细链接请见原文。</p></div></body></html>"


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


def _direct_companies(item: NewsItem) -> str:
    companies = item.direct_companies or item.company_layer
    return "、".join(companies[:6]) if companies else "-"


def _inferred_companies(item: NewsItem) -> str:
    if not item.inferred_companies:
        return "-"
    return "、".join(f"{company}（间接传导）" for company in item.inferred_companies[:6])


def _watch_variables(item: NewsItem) -> str:
    return "、".join(item.watch_variables[:4]) if item.watch_variables else "订单变化、收入兑现、成本变化"


def _meta_line(item: NewsItem) -> str:
    published = item.published_at or item.time_status or "时间不明"
    if "T" in published:
        published = published.replace("T", " ").split("+")[0].split(".")[0]
    discovery = item.discovery_method or "-"
    status = item.content_status or ("partial article" if item.is_partial else "正文可用")
    return f"{item.source}｜{published}｜{discovery}｜{status}"


def _company_cards(items: list[NewsItem]) -> list[str]:
    cards: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        for company in (item.direct_companies or item.company_layer)[:6]:
            key = (company, item.title)
            if key in seen:
                continue
            seen.add(key)
            impacts = "、".join(item.company_impact_type[:3]) if item.company_impact_type else "收入、成本或估值假设"
            cards.extend(
                [
                    "",
                    f"公司：{company}",
                    f"事件：{_clean(item.title)}",
                    f"影响方向：影响{impacts}",
                    f"影响路径：{_clean(item.transmission_chain or '该事件会传导至相关公司的收入、成本和估值假设。')}",
                    f"后续跟踪变量：{_watch_variables(item)}",
                ]
            )
    return cards[:60]


def _render_watchlist(watchlist: list[WatchItem], selected_urls: set[str]) -> list[str]:
    items = [item for item in watchlist if item.url not in selected_urls and _is_watchlist_item_readable(item)]
    if not items:
        return ["暂无进入观察池的弱信号。"]
    lines: list[str] = []
    for item in items[:5]:
        companies = item.direct_companies or item.company_layer
        inferred = "、".join(f"{company}（间接传导）" for company in item.inferred_companies[:4]) or "-"
        lines.extend(
            [
                "",
                f"事件：{_clean(item.title)}",
                f"层次：{item.industry_layer}",
                f"直接相关公司：{'、'.join(companies[:4]) if companies else '-'}",
                f"间接传导公司：{inferred}",
                f"信号类型：{item.signal_type}",
                f"状态：{item.status}",
                f"后续观察点：{'、'.join(item.watch_variables[:3]) if item.watch_variables else '-'}",
                f"链接：{item.url}",
            ]
        )
    return lines


def _is_core_signal_item(item: NewsItem) -> bool:
    content_status = item.content_status or ""
    body_available = not item.is_partial and not any(marker in content_status for marker in ("正文不可用", "仅基于", "订阅限制"))
    if item.time_status in {"time_unknown", "unknown"} and (item.discovery_method == "list_page" or not body_available):
        return False
    return True


def _is_watchlist_item_readable(item: WatchItem) -> bool:
    title = item.title.strip().lower()
    url = item.url.lower().rstrip("/")
    if not title or title in {
        "semianalysis",
        "core research",
        "data product",
        "data products",
        "chipbook",
        "events",
        "compliance policies",
        "compliance polices",
    }:
        return False
    if any(keyword in f"{title} {url}" for keyword in OBSERVATION_BLOCKLIST):
        return False
    if url.endswith(("semianalysis.com", "semianalysis.com/")):
        return False
    if "time_unknown" in item.status and item.discovery_method == "list_page":
        return False
    return True


def _suggested_action(item: NewsItem) -> str:
    if item.is_partial or item.time_status in {"time_unknown", "unknown"}:
        return "需人工复核"
    if item.importance == "高":
        return "跟踪"
    if item.importance == "中":
        return "加入观察池"
    return "暂不处理"


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


def send_email(subject: str, body: str, html_body: str | None = None) -> None:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing email environment variables: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["EMAIL_FROM"]
    message["To"] = os.environ["EMAIL_TO"]
    message.set_content(body, subtype="plain", charset="utf-8")
    if html_body:
        message.add_alternative(html_body, subtype="html", charset="utf-8")

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
