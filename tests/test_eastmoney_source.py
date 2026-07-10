from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.investment_filter import score_article
from src.models import Article
from src.sources.eastmoney import EASTMONEY_SOURCE_ID, EASTMONEY_SOURCE_NAME, EASTMONEY_SOURCE_TYPE
from src.utils import load_config


class EastmoneySourceTest(unittest.TestCase):
    def setUp(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "sources.yaml")
        self.source = next(source for source in config.sources if source.id == EASTMONEY_SOURCE_ID)
        self.start = datetime(2026, 7, 9, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.end = datetime(2026, 7, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.tracked = {
            "NVIDIA": ["NVIDIA", "英伟达"],
            "工业富联": ["工业富联"],
            "寒武纪": ["寒武纪"],
        }

    def make_article(self, title: str, content: str) -> Article:
        return Article(
            source_id=EASTMONEY_SOURCE_ID,
            source_name=EASTMONEY_SOURCE_NAME,
            source_language="zh",
            source_strategy="summarize",
            source_type=EASTMONEY_SOURCE_TYPE,
            title=title,
            url="https://finance.eastmoney.com/a/202607101234.html",
            published_at=self.end,
            time_status="published_within_window",
            content=content,
        )

    def test_source_config(self) -> None:
        self.assertEqual(self.source.id, "eastmoney")
        self.assertEqual(self.source.name, "东方财富")
        self.assertEqual(self.source.source_name, "东方财富")
        self.assertEqual(self.source.source_type, "market_news")

    def test_plain_market_move_is_not_core_signal(self) -> None:
        article = self.make_article(
            "AI 概念股午后拉升，多股涨幅居前",
            "AI 概念板块短线走强，多只个股上涨，未披露具体原因。",
        )
        decision = score_article(article, self.source, self.tracked, self.start, self.end)
        self.assertFalse(decision.keep)

    def test_investment_increment_can_enter_candidate_pool(self) -> None:
        article = self.make_article(
            "工业富联获得 AI 服务器大额订单并扩建产能",
            "工业富联公告获得 AI 服务器客户合同，并启动新产能建设，影响订单、收入和供应链供需。",
        )
        decision = score_article(article, self.source, self.tracked, self.start, self.end)
        self.assertTrue(decision.keep)
        self.assertTrue(decision.investment_signal_relevant)


if __name__ == "__main__":
    unittest.main()
