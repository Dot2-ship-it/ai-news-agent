from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import ARTIFACTS_DIR, render_preview_email
from src.emailer import render_email_html, render_email_text
from src.models import DailyDigest, NewsItem, WatchItem


class EmailSnapshotTest(unittest.TestCase):
    maxDiff = 2000

    def setUp(self) -> None:
        self.fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_digest_events.json"
        self.snapshot_path = PROJECT_ROOT / "tests" / "snapshots" / "sample_email_digest.txt"
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self.digest = DailyDigest.model_validate(data["digest"])
        self.source_stats = data["source_stats"]

    def render_email(self) -> str:
        return render_email_text(self.digest, source_stats=self.source_stats)

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
        for required_field in ("信息源：", "变化说明：", "投研含义：", "下一步验证："):
            self.assertIn(required_field, email)
        core_section = self._section(email, "核心信号 Top 3", "主线变化")
        self.assertEqual(core_section.count("事实摘要："), 3)
        self.assertEqual(core_section.count("增量判断："), 3)
        self.assertEqual(core_section.count("投研含义："), 3)
        self.assertEqual(core_section.count("下一步验证："), 3)
        self.assertFalse(any(line.startswith("验证：") for line in core_section.splitlines()))
        self.assertNotIn("发生了什么：", email)
        self.assertNotIn("为什么重要：", email)
        for removed_field in (
            "证据等级",
            "置信度",
            "evidence_level",
            "confidence_level",
            "原始主题标签",
            "今日事件：",
            "新增事件：",
            "历史数据不足，暂按本轮首次记录处理",
            "有效事件数",
            "抓取诊断",
            "成功源",
            "部分成功源",
            "失败源",
            "主要失败原因",
            "fetch_failed",
            "gdelt_rate_limited",
            "partial_success",
        ):
            self.assertNotIn(removed_field, email)
        self.assertNotIn("反转", email)
        self.assertTrue("本轮首次记录" in email or "历史数据不足，暂按本轮首次记录处理" in email)
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
        self.assertIn("信息源：", html)
        self.assertIn("变化说明：", html)
        for removed_heading in ("产业链层次", "公司层次", "本周继续追踪", "今日结论"):
            self.assertNotIn(removed_heading, html)
        for raw_field in (
            "discovered=",
            "fetched=",
            "filtered_by_relevance=",
            "gdelt_error=",
            "SMTP_",
            "API_KEY",
            "SECRET",
            "证据等级",
            "置信度",
            "evidence_level",
            "confidence_level",
            "原始主题标签",
            "今日事件：",
            "新增事件：",
            "历史数据不足，暂按本轮首次记录处理",
            "有效事件数",
            "抓取诊断",
            "成功源",
            "部分成功源",
            "失败源",
            "fetch_failed",
            "gdelt_rate_limited",
            "partial_success",
        ):
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

    def test_theme_changes_use_investment_thesis_and_real_events(self) -> None:
        email = self.render_email()
        mainline = self._section(email, "主线变化", "观察池")
        for placeholder in ("核心信号 1", "核心信号 2", "event 1", "item 1", "signal 1"):
            self.assertNotIn(placeholder, mainline)
        blocked_labels = (
            "HBM / DRAM / 存储供需",
            "AI capex 回报压力",
            "半导体与硬件供应链",
        )
        thesis_lines = [line.strip().lstrip("- ").strip() for line in mainline.splitlines() if "主线：" in line]
        self.assertTrue(thesis_lines)
        for line in thesis_lines:
            thesis = line.split("主线：", 1)[1]
            self.assertNotIn(thesis, blocked_labels)
            self.assertGreater(len(thesis), 10)
            self.assertRegex(thesis, r"（新增|延续|升温|降温|待确认）")
        self.assertNotIn("新增 1 条证据", mainline)
        self.assertNotIn("变化说明：本轮首次记录", mainline)
        self.assertNotIn("今日事件：", mainline)
        self.assertNotIn("新增事件：", mainline)
        self.assertIn("HBM 与 DRAM 产能再分配可能影响 AI 硬件供需", mainline)
        self.assertIn("“HBM 扩产是否会挤压标准型 DRAM 供给”", mainline)
        self.assertIn("认知", mainline) if "认知" in mainline else self.assertIn("此前市场", mainline)

    def test_theme_verification_points_are_structured(self) -> None:
        email = self.render_email()
        mainline = self._section(email, "主线变化", "观察池")
        verification_points = [
            line.strip()
            for line in mainline.splitlines()
            if line.strip().startswith("- ") and "：" in line and "主线：" not in line
        ]
        self.assertGreaterEqual(len(verification_points), 3)
        bare_variables = {"HBM 价格", "DRAM 产能", "GPU 交付周期", "出口许可范围", "受限地区", "中国收入"}
        for point in verification_points:
            text = point.lstrip("- ").strip()
            self.assertNotIn(text, bare_variables)
            self.assertIn("观察", text)
            self.assertRegex(text, r"说明|验证|意味着|若")

    def test_watchlist_link_and_relevance_rules(self) -> None:
        digest = DailyDigest(
            subject="AI 投研情报日报｜2026-07-09",
            opening_summary="",
            trend="",
            items=[
                NewsItem(
                    importance="高",
                    title="Broken QbitAI AI 芯片订单消息",
                    source="量子位 QbitAI",
                    url="https://www.qbitai.com/2026/07/446778.html",
                    core_fact="AI 芯片订单消息链接不可用。",
                    important_meaning="影响 AI 芯片供应链订单判断。",
                    link_status="invalid",
                )
            ],
            watchlist=[
                WatchItem(
                    title="Broken QbitAI AI 芯片订单消息",
                    url="https://www.qbitai.com/2026/07/446778.html",
                    source="量子位 QbitAI",
                    industry_layer="半导体与硬件供应链",
                    signal_type="订单",
                    score=70,
                    status="watch",
                    watch_variables=["AI 芯片订单"],
                    ai_investment_relevance="AI 芯片订单与半导体供应链投资变量相关。",
                    current_limit="链接不可用",
                    link_status="invalid",
                ),
                WatchItem(
                    title="AI 服务器订单待确认",
                    url="https://example.com/unknown-ai-server",
                    source="东方财富",
                    industry_layer="半导体与硬件供应链",
                    signal_type="订单",
                    score=65,
                    status="watch",
                    watch_variables=["AI 服务器订单"],
                    ai_investment_relevance="AI 服务器订单与算力供应链收入变量相关。",
                    current_limit="来源单一 / 细节不足",
                    link_status="unknown",
                ),
                WatchItem(
                    title="AI 数据中心资本开支线索缺少原文链接",
                    url="",
                    source="unknown",
                    industry_layer="AI Capex / 算力基础设施",
                    signal_type="资本开支",
                    score=64,
                    status="watch",
                    watch_variables=["资本开支"],
                    ai_investment_relevance="AI 数据中心资本开支与算力基础设施投资变量相关。",
                    current_limit="来源单一 / 细节不足",
                    link_status="invalid",
                ),
                WatchItem(
                    title="长征十号乙首飞在即 可回收技术突破将至？机构紧盯多只概念股",
                    url="https://finance.eastmoney.com/a/space.html",
                    source="东方财富",
                    industry_layer="二级市场与资金面",
                    signal_type="市场异动",
                    score=65,
                    status="watch",
                    watch_variables=["概念股"],
                    link_status="valid",
                ),
                WatchItem(
                    title="商业航天公司融资进展引发机构紧盯多只概念股",
                    url="https://finance.eastmoney.com/a/commercial-space.html",
                    source="东方财富",
                    industry_layer="二级市场与资金面",
                    signal_type="融资",
                    score=65,
                    status="watch",
                    watch_variables=["融资"],
                    ai_investment_relevance="商业航天融资与泛投资主题相关。",
                    current_limit="未说明 AI 投资相关点",
                    link_status="valid",
                ),
            ],
        )
        email = render_email_text(digest, source_stats=[])
        html = render_email_html(digest, source_stats=[])
        for rendered in (email, html):
            for removed_field in (
                "标题：",
                "来源：",
                "AI 投资相关点：",
                "当前限制：",
                "处理建议：",
                "链接状态：",
                "链接：",
                "Synthetic",
            ):
                self.assertNotIn(removed_field, rendered)
        self.assertNotIn("Broken QbitAI", email)
        self.assertNotIn("446778.html", email)
        self.assertIn("AI 服务器订单待确认｜半导体与硬件供应链", email)
        self.assertIn("关注点：AI 服务器订单与算力供应链收入变量相关", email)
        self.assertIn("链接待确认", email)
        self.assertNotIn("AI 数据中心资本开支线索缺少原文链接\n  链接", email)
        self.assertNotIn("长征十号乙", email)
        self.assertNotIn("商业航天", email)
        self.assertNotIn("航天", email)
        self.assertNotIn("火箭", email)
        self.assertNotIn("可回收技术", email)

    def test_preview_artifacts_keep_diagnostics_out_of_email(self) -> None:
        body = render_preview_email()
        html = (ARTIFACTS_DIR / "preview_email.html").read_text(encoding="utf-8")
        txt = (ARTIFACTS_DIR / "preview_email.txt").read_text(encoding="utf-8")
        diagnostics_path = ARTIFACTS_DIR / "crawl_diagnostics.json"
        self.assertTrue(diagnostics_path.exists())
        diagnostics = diagnostics_path.read_text(encoding="utf-8")
        self.assertIn("source_name", diagnostics)
        for rendered in (body, html, txt):
            self.assertIn("今日摘要", rendered)
            self.assertIn("核心信号", rendered)
            self.assertIn("主线变化", rendered)
            self.assertIn("观察池", rendered)
            for internal in (
                "有效事件数",
                "抓取诊断",
                "成功源",
                "部分成功源",
                "失败源",
                "fetch_failed",
                "gdelt_rate_limited",
                "partial_success",
                "标题：",
                "来源：",
                "AI 投资相关点：",
                "当前限制：",
                "处理建议：",
                "链接状态：",
                "链接：",
                "Synthetic",
            ):
                self.assertNotIn(internal, rendered)

    @staticmethod
    def _section(email: str, start: str, end: str) -> str:
        return email.split(start, 1)[1].split(end, 1)[0]


if __name__ == "__main__":
    unittest.main()
