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
        self.assertIn("抓取概览", email)
        self.assertIn("一、今日核心信号 Top 3", email)
        self.assertIn("二、主线变化", email)
        self.assertIn("六、本周继续追踪", email)
        self.assertEqual(email.count("发生了什么："), 3)
        self.assertEqual(email.count("为什么重要："), 3)
        self.assertEqual(email.count("后续看什么："), 3)
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
        self.assertIn("今日核心信号 Top 3", html)
        self.assertIn("主线变化", html)
        self.assertIn("本周继续追踪", html)
        self.assertIn("查看原文", html)
        for raw_field in ("discovered=", "fetched=", "filtered_by_relevance=", "gdelt_error=", "SMTP_", "API_KEY", "SECRET"):
            self.assertNotIn(raw_field, html)
        html_without_href_urls = re.sub(r'href="https?://[^"]+"', 'href=""', html)
        self.assertNotIn("https://", html_without_href_urls)
        self.assertIsInstance(render_email_text(self.digest, source_stats=self.source_stats), str)


if __name__ == "__main__":
    unittest.main()
