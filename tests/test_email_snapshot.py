from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import render_diagnostics_text
from src.emailer import render_email_html, render_email_text
from src.models import DailyDigest


class EmailSnapshotTest(unittest.TestCase):
    maxDiff = 2000

    def setUp(self) -> None:
        self.fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_digest_events.json"
        self.snapshot_path = PROJECT_ROOT / "tests" / "snapshots" / "sample_email_digest.txt"
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self.digest = DailyDigest.model_validate(data["digest"])
        self.source_stats = data["source_stats"]

    def render_email(self) -> str:
        body = render_email_text(self.digest, source_stats=self.source_stats)
        return f"{body}\n{render_diagnostics_text(self.source_stats)}"

    def render_html(self) -> str:
        return render_email_html(self.digest, source_stats=self.source_stats)

    def test_snapshot_matches(self) -> None:
        self.assertEqual(self.snapshot_path.read_text(encoding="utf-8").strip(), self.render_email().strip())

    def test_required_sections_and_safety(self) -> None:
        email = self.render_email()
        self.assertIn("今日摘要", email)
        self.assertIn("核心信号 Top 3", email)
        self.assertIn("主线变化", email)
        self.assertIn("观察池", email)
        self.assertIn("抓取诊断", email)
        core_section = self._section(email, "核心信号 Top 3", "主线变化")
        self.assertEqual(core_section.count("事实摘要："), 3)
        self.assertEqual(core_section.count("增量判断："), 3)
        self.assertEqual(core_section.count("投研含义："), 3)
        self.assertEqual(core_section.count("下一步验证："), 3)
        self.assertNotIn("发生了什么：", email)
        self.assertNotIn("为什么重要：", email)
        for removed_heading in ("产业链层次", "公司层次", "本周继续追踪", "今日结论"):
            self.assertNotIn(removed_heading, email)
        self.assertLessEqual(len(email), 12000)
        for raw_field in ("discovered=", "fetched=", "filtered_by_relevance=", "SMTP_", "API_KEY", "SECRET"):
            self.assertNotIn(raw_field, email)
        self.assertNotIn("只有列表页的未知时间泛页面", email)

    def test_html_email_structure_and_safety(self) -> None:
        html = self.render_html()
        self.assertIn("<html", html)
        self.assertIn("max-width: 680px", html)
        self.assertIn("font-family", html)
        self.assertIn("background-color", html)
        self.assertIn("核心信号 Top 3", html)
        self.assertIn("主线变化", html)
        self.assertIn("今日摘要", html)
        self.assertIn("观察池", html)
        self.assertIn("查看原文", html)
        for removed_heading in ("产业链层次", "公司层次", "本周继续追踪", "今日结论"):
            self.assertNotIn(removed_heading, html)
        for raw_field in ("discovered=", "fetched=", "filtered_by_relevance=", "gdelt_error=", "SMTP_", "API_KEY", "SECRET"):
            self.assertNotIn(raw_field, html)
        html_without_href_urls = re.sub(r'href="https?://[^"]+"', 'href=""', html)
        self.assertNotIn("https://", html_without_href_urls)
        self.assertIsInstance(render_email_text(self.digest, source_stats=self.source_stats), str)

    def test_summary_and_repetition_control(self) -> None:
        email = self.render_email()
        summary = self._section(email, "今日摘要", "核心信号 Top 3")
        summary_text = summary.replace("\n", "")
        self.assertLessEqual(len(summary_text), 120)
        for item in self.digest.items[:3]:
            self.assertNotIn(item.title, summary)
            self.assertLessEqual(email.count(item.title), 2)
        mainline = self._section(email, "主线变化", "观察池")
        self.assertNotIn("事实摘要：", mainline)

    @staticmethod
    def _section(email: str, start: str, end: str) -> str:
        return email.split(start, 1)[1].split(end, 1)[0]


if __name__ == "__main__":
    unittest.main()
