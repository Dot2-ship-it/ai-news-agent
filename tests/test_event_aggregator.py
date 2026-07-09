from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.event_aggregator import build_event_bundle, event_from_item, load_themes
from src.investment_filter import derive_report_fields
from src.models import DailyDigest, NewsItem, WatchItem


def make_item(
    title: str,
    content: str,
    source: str = "SemiAnalysis",
    url: str = "https://example.com/article/",
    score: int = 90,
    time_status: str = "published_within_window",
    discovery_method: str = "rss",
    is_partial: bool = False,
) -> NewsItem:
    fields = derive_report_fields(title=title, content=content, url=url)
    return NewsItem(
        importance="高",
        title=title,
        source=source,
        url=url,
        core_fact=content,
        important_meaning="影响产业链供需、资本开支和估值预期。",
        key_points=["用于规则测试。"],
        published_at="2026-07-09T08:00:00+08:00",
        time_status=time_status,
        discovery_method=discovery_method,
        investment_score=score,
        is_partial=is_partial,
        **fields,
    )


def make_watch(title: str, url: str) -> WatchItem:
    fields = derive_report_fields(title=title, content=title, url=url)
    return WatchItem(
        title=title,
        url=url,
        source="SemiAnalysis",
        industry_layer=str(fields["industry_layer"]),
        company_layer=list(fields["company_layer"]),
        direct_companies=list(fields["direct_companies"]),
        inferred_companies=list(fields["inferred_companies"]),
        watch_companies=list(fields["watch_companies"]),
        signal_type=str(fields["signal_type"]),
        score=60,
        status="time_unknown",
        watch_variables=list(fields["watch_variables"]),
        discovery_method="list_page",
    )


class EventAggregatorRulesTest(unittest.TestCase):
    def test_sk_hynix_memory_event_rules(self) -> None:
        item = make_item(
            "SK海力士放缓 HBM4 转向 DRAM",
            "SK海力士调整 HBM4 与 DRAM 产能配置，影响存储供需和资本开支。",
        )
        event = event_from_item(item, load_themes())
        self.assertEqual(event.industry_layer, "半导体与硬件供应链")
        for keyword in ("HBM", "DRAM", "存储供需", "资本开支"):
            self.assertIn(keyword, event.signal_type)
        self.assertIn("SK Hynix", event.direct_companies)
        self.assertNotIn("Anthropic", event.direct_companies)

    def test_coreweave_data_center_event_rules(self) -> None:
        item = make_item(
            "CoreWeave data center lease expands",
            "CoreWeave expands data center lease with MW power and AI cloud capacity.",
            source="Data Center Dynamics",
        )
        event = event_from_item(item, load_themes())
        self.assertEqual(event.industry_layer, "数据中心与电力")

    def test_semianalysis_generic_pages_do_not_enter_observation_pool(self) -> None:
        watch_items = [
            make_watch("ChipBook", "https://semianalysis.com/chipbook/"),
            make_watch("Core Research", "https://semianalysis.com/core-research/"),
            make_watch("Data Product", "https://semianalysis.com/semianalysis-data-products/"),
            make_watch("Events", "https://semianalysis.com/semianalysis-events/"),
            make_watch("Compliance Policies", "https://semianalysis.com/compliance-policies/"),
        ]
        digest = DailyDigest(subject="AI 投研情报日报｜2026-07-09", opening_summary="", trend="", items=[], watchlist=watch_items)
        bundle = build_event_bundle(digest)
        self.assertEqual(bundle.watch_events, [])

    def test_low_confidence_d_or_e_event_not_in_core(self) -> None:
        low_item = make_item(
            "Unconfirmed AI cloud rumor",
            "Rumor says an AI cloud pricing signal may change.",
            source="Unknown Aggregator",
            url="https://example.com/rumor/",
            score=99,
            time_status="time_unknown",
            discovery_method="list_page",
            is_partial=True,
        )
        digest = DailyDigest(subject="AI 投研情报日报｜2026-07-09", opening_summary="", trend="", items=[low_item])
        bundle = build_event_bundle(digest)
        self.assertEqual(bundle.core_events, [])

    def test_direct_and_peer_companies_are_separate(self) -> None:
        item = make_item(
            "SK海力士放缓 HBM4 转向 DRAM",
            "SK海力士调整 HBM4 与 DRAM 产能配置，影响存储供需。",
        )
        event = event_from_item(item, load_themes())
        self.assertEqual(event.direct_companies, ["SK Hynix"])
        self.assertIn("NVIDIA", event.peer_companies)
        self.assertNotIn("NVIDIA", event.direct_companies)


if __name__ == "__main__":
    unittest.main()
