from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.emailer import render_email_text
from src.investment_filter import derive_report_fields
from src.models import DailyDigest, NewsItem, WatchItem
from main import render_diagnostics_text


def make_item(
    title: str,
    content: str,
    importance: str = "高",
    source: str = "Synthetic",
    url: str = "https://example.com/article",
    time_status: str = "published_within_window",
    discovery_method: str = "rss",
    is_partial: bool = False,
    content_status: str | None = None,
) -> NewsItem:
    fields = derive_report_fields(title=title, content=content, url=url)
    return NewsItem(
        importance=importance,  # type: ignore[arg-type]
        title=title,
        source=source,
        url=url,
        core_fact=content[:90],
        important_meaning="影响相关公司的收入、资本开支、供需和市场预期，需要跟踪后续兑现。",
        key_points=["该事项具备公司、产业链和验证变量。"],
        time_status=time_status,
        discovery_method=discovery_method,
        is_partial=is_partial,
        content_status=content_status,
        investment_score=88,
        **fields,
    )


def make_watch(title: str, url: str, status: str = "watch", method: str = "rss") -> WatchItem:
    fields = derive_report_fields(title=title, content=title, url=url)
    return WatchItem(
        title=title,
        url=url,
        source="Synthetic",
        industry_layer=str(fields["industry_layer"]),
        company_layer=list(fields["company_layer"]),
        direct_companies=list(fields["direct_companies"]),
        inferred_companies=list(fields["inferred_companies"]),
        watch_companies=list(fields["watch_companies"]),
        signal_type=str(fields["signal_type"]),
        score=60,
        status=status,
        watch_variables=list(fields["watch_variables"]),
        discovery_method=method,
    )


def main() -> None:
    sk_title = "SK海力士放缓 HBM4 转向 DRAM"
    sk_content = (
        "SK海力士调整 HBM4 扩产节奏并转向 DRAM 产能配置，影响 HBM、DRAM、"
        "NVIDIA 和 AMD GPU 供应链的存储供需与资本开支假设。"
    )
    sk_item = make_item(sk_title, sk_content, source="SemiAnalysis", url="https://semianalysis.com/sk-hynix-hbm4-dram/")
    core_items = [
        sk_item,
        make_item(
            "CoreWeave 数据中心租赁合同扩大",
            "CoreWeave 扩大数据中心租赁合同，涉及 AI cloud capacity、MW power、GPU clusters 和客户 backlog。",
            source="DCD",
            url="https://www.datacenterdynamics.com/en/news/coreweave-lease/",
        ),
        make_item(
            "美国扩大 AI 芯片出口限制",
            "美国商务部扩大 AI chip export control 和出口许可要求，影响 NVIDIA、AMD 中国收入和合规风险。",
            source="Reuters",
            url="https://www.reuters.com/technology/artificial-intelligence/export-control/",
        ),
    ]
    blocked_core = make_item(
        "只有列表页的未知时间泛页面",
        "list page only summary",
        importance="中",
        source="SemiAnalysis",
        url="https://semianalysis.com/",
        time_status="time_unknown",
        discovery_method="list_page",
        is_partial=True,
        content_status="正文不可用，仅基于标题/列表页信息",
    )
    watchlist = [
        make_watch("SemiAnalysis", "https://semianalysis.com/", status="time_unknown", method="list_page"),
        make_watch("Data Product", "https://semianalysis.com/data-product/", status="time_unknown", method="list_page"),
        make_watch("Events", "https://semianalysis.com/events/", status="watch"),
        make_watch("Compliance Policies", "https://semianalysis.com/compliance-policies/", status="watch"),
        make_watch("Core Research", "https://semianalysis.com/core-research/", status="watch"),
        make_watch("ChipBook", "https://semianalysis.com/chipbook/", status="watch"),
        make_watch(
            "Oracle AI 云租赁价格出现新线索",
            "https://example.com/oracle-ai-cloud-lease/",
            status="time_unknown",
            method="rss",
        ),
    ]
    digest = DailyDigest(
        subject="AI 投研情报日报｜2026-07-09",
        opening_summary=(
            "- 本期抓取范围：北京时间 2026-07-08 10:00 至 2026-07-09 10:00。\n"
            "- 今日共抓取候选链接 20 条，成功提取正文 12 篇，通过投研过滤保留 7 篇，最终精选 5 条进入日报。\n"
            "- 今日要点一：算力供应链继续围绕 HBM、数据中心和出口限制定价。\n"
            "- 今日要点二：云厂商 capex 和数据中心合同仍是核心验证变量。\n"
            "- 今日值得继续关注：GPU 交付周期、HBM 价格和出口许可范围。"
        ),
        trend="",
        items=[*core_items, blocked_core],
        watchlist=watchlist,
    )
    source_stats = [
        {"source_name": "NVIDIA IR", "status": "success"},
        {"source_name": "DCD", "status": "partial_success", "error_type": "body_unavailable"},
        {"source_name": "Reuters", "status": "fetch_failed", "error_type": "HTTPStatusError"},
    ]
    body = render_email_text(digest, source_stats=source_stats)
    diagnostics = render_diagnostics_text(source_stats)
    full_email = f"{body}\n{diagnostics}"

    assert "一、今日核心信号 Top 3" in body
    assert "今日核心信号 Top 5" not in body
    assert sk_item.industry_layer == "半导体与硬件供应链"
    assert sk_item.industry_layer != "政策 / 监管 / 出口管制"
    assert sk_item.signal_type == "存储供需 / HBM / DRAM / 资本开支"
    assert "SK Hynix" in sk_item.direct_companies or "SK海力士" in sk_item.direct_companies
    assert "Anthropic" not in sk_item.direct_companies
    assert "Anthropic" not in sk_item.inferred_companies
    for blocked in ("SemiAnalysis", "Data Product", "Events", "Compliance Policies", "Core Research", "ChipBook"):
        observation_section = body.split("四、观察池", 1)[-1]
        assert blocked not in observation_section
    for raw_field in ("discovered=", "fetched=", "filtered_by_relevance="):
        assert raw_field not in full_email
    assert "只有列表页的未知时间泛页面" not in body

    print(full_email)
    print("\nQUALITY_TEST_SUMMARY")
    print("top_n_dynamic=passed")
    print("sk_hynix_layer=passed")
    print("sk_hynix_direct_companies=passed")
    print("observation_generic_pages_filtered=passed")
    print("diagnostics_compact=passed")
    print("time_unknown_list_page_not_core=passed")


if __name__ == "__main__":
    main()
