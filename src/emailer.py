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
    if bundle.theme_changes:
        strongest_events = _events_for_theme(bundle, bundle.theme_changes[0].theme_id)
        strongest = build_theme_thesis(bundle.theme_changes[0].theme_id, strongest_events)
    else:
        strongest = "暂无明确主线"
    variables = _dedup_variables(bundle.core_events)[:2]
    watch_count = len(bundle.watch_events)
    return [
        f"- 主线最强：{strongest}。",
        f"- 关键验证：{'、'.join(variables) if variables else '后续官方披露'}。",
        f"- 暂不纳入主线：观察池 {watch_count} 条，待补充事实或交叉验证。",
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
              <div class="muted">{escape(event.industry_layer)}｜{escape(_theme_labels(event, bundle))}</div>
              <p><span class="label">来源：</span>{escape(event.source)}</p>
              <p><span class="label">验证：</span>{escape(_verification_status(event))}</p>
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
        thesis = build_theme_thesis(change.theme_id, events)
        industry_layer = _theme_industry_layer(events)
        verification_items = "".join(
            f"<li>{escape(item)}</li>" for item in _theme_verification_points(change.theme_id, events)
        )
        items.append(
            f"""
            <div class="compact-item">
              <div><strong>主线：{escape(thesis)}（{escape(_theme_status_inline(change.signal_status))}）</strong></div>
              <div>产业链：{escape(industry_layer)}</div>
              <div>变化说明：{escape(_theme_change_explanation(change.theme_id, events))}</div>
              <div>投资含义：{escape(_theme_implication(change.theme_id, thesis))}</div>
              <div>下一步验证：<ul>{verification_items}</ul></div>
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
        f"【{event.importance}】{_clean(event.title)}",
        f"产业链：{event.industry_layer}",
        f"主线：{_theme_labels(event, bundle)}",
        f"来源：{event.source}",
        f"验证：{_verification_status(event)}",
        "",
        f"事实摘要：{_brief(event.fact, 120)}",
        f"增量判断：{_incremental_judgment(event)}",
        f"投研含义：{_brief(event.investment_implication, 140)}",
        f"影响公司：{_impact_companies(event)}",
        "下一步验证：",
    ]
    for variable in event.follow_up_variables[:3]:
        lines.append(f"- {variable.name}：{variable.direction_to_watch}，{variable.why}")
    lines.append(f"链接：{event.canonical_url}")
    return lines


def _render_theme_changes(bundle: EventBundle) -> list[str]:
    if not bundle.theme_changes:
        return ["暂无可归纳的主线变化。"]
    lines = []
    for change in bundle.theme_changes:
        events = _events_for_theme(bundle, change.theme_id)
        thesis = build_theme_thesis(change.theme_id, events)
        lines.extend(
            [
                f"- 主线：{thesis}（{_theme_status_inline(change.signal_status)}）",
                f"  产业链：{_theme_industry_layer(events)}",
                f"  变化说明：{_theme_change_explanation(change.theme_id, events)}",
                f"  投资含义：{_theme_implication(change.theme_id, thesis)}",
                "  下一步验证：",
            ]
        )
        for item in _theme_verification_points(change.theme_id, events):
            lines.append(f"  - {item}")
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
                f"信号：{status_label(event.signal_status)}",
                f"来源：{event.source}",
                f"验证：{_verification_status(event)}",
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


def build_theme_thesis(theme_id: str, events: list[DigestEvent]) -> str:
    if theme_id == "hbm_dram_supply":
        return "HBM 与 DRAM 产能再分配可能影响 AI 硬件供需"
    if theme_id == "ai_capex_roi":
        return "AI 基建扩张仍需订单和利用率验证"
    if theme_id == "gpu_cloud_supply":
        return "算力供需变化继续牵动云租赁价格"
    if theme_id == "data_center_power":
        return "数据中心扩张受电力和交付节奏约束"
    if theme_id == "china_model_commercialization":
        return "中国大模型商业化转向收入验证"
    if theme_id == "china_ai_chip_substitution":
        return "国产 AI 芯片替代进入供给验证阶段"
    if theme_id == "robotics_mass_production":
        return "机器人量产进入交付质量验证阶段"
    if theme_id == "ai_app_api_revenue":
        return "AI 应用商业化需要 API 收入验证"
    if theme_id == "export_control_geopolitics":
        return "出口限制继续改变AI芯片收入预期"
    if theme_id == "ai_company_valuation":
        return "AI 公司估值重定价取决于收入兑现"
    text = " ".join(
        f"{event.title} {event.signal_type} {event.industry_layer} {event.fact}"
        for event in events[:3]
    )
    if any(keyword in text for keyword in ("HBM", "DRAM", "存储")):
        return "HBM 与 DRAM 产能再分配可能影响 AI 硬件供需"
    if events:
        return _brief(f"{events[0].signal_type}影响{events[0].industry_layer}投资假设", 35)
    return "AI产业链信号仍需后续事实验证"


def summarize_event_for_theme(event: DigestEvent) -> str:
    title = _clean(event.title)
    replacements = (
        ("SK海力士放缓 HBM4 转向 DRAM", "SK海力士放缓HBM产线转换"),
        ("CoreWeave 数据中心租赁合同扩大", "CoreWeave扩大数据中心租赁合同"),
        ("美国扩大 AI 芯片出口限制", "美国扩大AI芯片出口限制"),
    )
    for old, new in replacements:
        if old in title:
            return new
    title = re.sub(r"\s+", "", title)
    return title[:30] if len(title) > 30 else title


def _incremental_judgment(event: DigestEvent) -> str:
    return f"{status_label(event.signal_status)}，核心变量为 {event.signal_type}。"


def _verification_status(event: DigestEvent) -> str:
    source = event.source.lower()
    if any(keyword in source for keyword in ("sec", "edgar", "investor", "ir", "official", "公告", "财报", "监管文件")):
        return "已交叉验证"
    if any(separator in event.source for separator in ("、", "|", ",")):
        return "已交叉验证"
    if event.source == "东方财富":
        return "单一来源"
    if event.is_watch or event.is_partial or event.published_at is None:
        return "待确认"
    return "单一来源"


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


def _theme_verification_points(theme_id: str, events: list[DigestEvent]) -> list[str]:
    variables = _dedup_variables(events)
    if theme_id == "hbm_dram_supply":
        return [
            "HBM 价格：观察 HBM3E / HBM4 报价是否继续上行；若维持强势，说明 AI 存储需求仍紧。",
            "标准型 DRAM 价格：观察 DDR5 / 服务器 DRAM 是否同步上涨；若上行，验证 HBM 扩产挤压传统供给。",
            "SK 海力士产能指引：观察公司是否调整 HBM 与 DRAM 产能配置；这是验证产能再分配的核心证据。",
            "三星 / 美光动作：观察是否跟随调整 HBM、DRAM 资本开支；若跟随，说明行业周期变化。",
        ]
    if theme_id == "gpu_cloud_supply":
        return [
            "GPU 交付周期：观察 NVIDIA / 云厂商交付是否延长；若变慢，说明算力供给仍受瓶颈约束。",
            "GPU 云租赁价格：观察主流实例租赁价格是否上行；若价格坚挺，验证算力供需仍偏紧。",
            "算力利用率：观察云厂商利用率和排队情况；若维持高位，说明需求仍能消化新增供给。",
        ]
    if theme_id == "ai_capex_roi":
        return [
            "云厂商 capex 指引：观察是否继续上修 AI 基建投入；若上修，说明扩张周期仍未结束。",
            "订单与客户续约：观察大客户合同和续约节奏；若订单增强，验证 AI 基建 ROI 仍可兑现。",
            "AI 云利用率：观察 GPU 集群利用率是否维持高位；若回落，意味着 capex 回报压力上升。",
        ]
    if theme_id == "data_center_power":
        return [
            "MW/GW 签约容量：观察新增电力和机柜容量是否落地；若兑现，说明扩张约束有所缓解。",
            "电力接入进度：观察并网、变电站和电力采购公告；若延迟，验证电力仍是交付瓶颈。",
            "液冷和建设成本：观察数据中心单位建设成本变化；若上行，意味着 AI 云毛利率承压。",
        ]
    if theme_id == "export_control_geopolitics":
        return [
            "出口许可范围：观察监管是否扩大受限芯片和地区；若扩大，说明相关收入折价压力上升。",
            "中国收入披露：观察 NVIDIA / AMD 中国收入变化；若下滑，验证出口限制影响开始兑现。",
            "替代采购动作：观察国产芯片客户导入和订单公告；若增加，说明替代逻辑升温。",
        ]
    if theme_id in {"china_model_commercialization", "ai_app_api_revenue"}:
        return [
            "API 收入：观察模型厂商是否披露付费调用增长；若增长，说明商业化质量改善。",
            "企业客户续约：观察大客户续约和扩容合同；若续约增强，验证需求不是一次性试用。",
            "推理成本：观察单位调用成本是否下降；若下降，意味着毛利率和定价空间改善。",
        ]
    if theme_id == "china_ai_chip_substitution":
        return [
            "客户导入公告：观察云厂商和模型厂商是否采购国产芯片；若增加，验证替代进入订单阶段。",
            "推理性能指标：观察实际部署性能和稳定性；若接近主流 GPU，说明替代空间扩大。",
            "供应链产能：观察封装、存储和代工配套进度；若同步改善，验证供给可持续。",
        ]
    if theme_id == "robotics_mass_production":
        return [
            "交付订单：观察机器人厂商量产订单是否落地；若放量，说明商业化从样机进入交付。",
            "BOM 成本：观察核心零部件成本是否下降；若下降，验证毛利率改善空间。",
            "应用场景复购：观察工业或服务场景复购情况；若复购增加，说明需求质量提升。",
        ]
    if theme_id == "ai_company_valuation":
        return [
            "估值倍数：观察 AI 公司收入倍数是否继续压缩；若压缩，说明市场提高兑现要求。",
            "收入增速：观察财报收入和指引是否支撑估值；若不及预期，验证重定价压力。",
            "资金流向：观察 AI 资产资金流入是否恢复；若回流，意味着风险偏好修复。",
        ]
    fallback = []
    for variable in variables[:3]:
        fallback.append(f"{variable}：观察相关公告、价格或订单是否变化；若持续变化，说明该主线仍需跟踪验证。")
    return fallback or ["后续官方披露：观察公司公告和财报指引；若出现新增事实，验证主线是否成立。"]


def _theme_today_events(change, bundle: EventBundle) -> str:
    if not change.today_event_titles:
        return "无。"
    return "；".join(_event_summaries(change.today_event_titles, bundle)) + "。"


def _theme_new_events(change, bundle: EventBundle) -> str:
    if not change.new_event_titles:
        return "无。今日事件仅延续此前主线，未改变判断。"
    titles = "；".join(f"“{title}”" for title in _event_summaries(change.new_event_titles, bundle)[:2])
    if not change.history_available:
        return f"历史数据不足，暂按本轮首次记录处理；本轮新增事件为{titles}。"
    return f"相较过去记录，本轮首次出现{titles}这一信号。"


def _event_summaries(titles: list[str], bundle: EventBundle) -> list[str]:
    summaries: list[str] = []
    for title in titles[:3]:
        summary = _event_summary(title, bundle)
        if summary not in summaries:
            summaries.append(summary)
    return summaries or [_clean(titles[0]) if titles else "无"]


def _event_summary(title: str, bundle: EventBundle) -> str:
    for event in [*bundle.core_events, *bundle.watch_events]:
        if event.title == title:
            return summarize_event_for_theme(event)
    return _clean(title)[:30]


def _theme_change_explanation(theme_id: str, events: list[DigestEvent]) -> str:
    if not events:
        return "主线延续，但今日没有足够的新事实改变判断。"
    event = events[0]
    if theme_id == "hbm_dram_supply":
        return "此前市场更关注 HBM 扩产是否足够支撑 AI 训练需求；该事件把问题推进到“HBM 扩产是否会挤压标准型 DRAM 供给”，因此主线从单一 HBM 紧缺扩展为存储产能结构再平衡。"
    if theme_id == "data_center_power":
        return "此前市场更关注 AI 云需求扩张；本轮事件把验证重点推进到数据中心租赁、电力接入和机柜交付能否同步兑现。"
    if theme_id == "gpu_cloud_supply":
        return "此前市场更关注 GPU 供给总量；本轮事件把问题推进到云租赁价格、利用率和交付周期是否能支撑供需紧平衡。"
    if theme_id == "ai_capex_roi":
        return "此前市场更关注云厂商是否继续加码 AI 基建；本轮事件要求用订单、利用率和租赁价格验证投入回报。"
    if theme_id == "export_control_geopolitics":
        return "此前市场更关注芯片需求强度；监管事件把变量推进到销售区域、出口许可和合规成本对收入预期的影响。"
    if theme_id in {"china_model_commercialization", "ai_app_api_revenue"}:
        return "此前市场更关注用户增长和产品发布；本轮信息把验证重点转向 API 收入、企业客户续约和商业化质量。"
    if theme_id == "china_ai_chip_substitution":
        return "此前市场更关注国产替代叙事；本轮信息需要进一步验证实际供给、客户导入和推理芯片性能。"
    if theme_id == "robotics_mass_production":
        return "此前市场更关注样机和演示；本轮信息需要进一步验证量产节奏、订单交付和单位经济性。"
    return _brief(event.investment_implication, 90)


def _theme_implication(theme_id: str, thesis: str) -> str:
    if theme_id == "hbm_dram_supply":
        return "若属实，AI 硬件链约束不只在 GPU，也可能体现在存储价格、HBM 产能分配和先进封装节奏上。"
    if theme_id == "data_center_power":
        return "影响 AI 云扩张节奏、租赁成本和基建 ROI 假设。"
    if theme_id == "gpu_cloud_supply":
        return "影响 GPU 云租赁价格、算力利用率和 AI 云公司收入兑现。"
    if theme_id == "export_control_geopolitics":
        return "影响销售区域、供给可得性和估值折价。"
    if theme_id == "ai_capex_roi":
        return "影响云厂商资本开支持续性和 AI 基建估值。"
    if theme_id == "ai_company_valuation":
        return "影响市场风险偏好和 AI 资产重定价。"
    return f"{thesis}，影响相关公司的收入兑现、成本假设和估值预期。"


def _theme_status_inline(status: str) -> str:
    label = status_label(status)
    return "新增" if label == "本轮首次记录" else label


def _theme_industry_layer(events: list[DigestEvent]) -> str:
    for event in events:
        if event.industry_layer:
            return event.industry_layer
    return "未归类"


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
