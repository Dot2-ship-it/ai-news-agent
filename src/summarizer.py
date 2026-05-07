from __future__ import annotations

import json
import os
import re

from openai import OpenAI

from .models import Article, DailyDigest


BANNED_STYLE_WORDS = [
    "实锤",
    "震动",
    "炸裂",
    "狂飙",
    "暴跌",
    "认罪",
    "零元购",
    "当庭认罪",
    "引发震动",
    "彻底改变",
    "决定性",
    "重塑",
    "证明了",
    "重磅",
    "颠覆",
    "封神",
]


class NewsSummarizer:
    def __init__(self, model: str) -> None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("Missing DEEPSEEK_API_KEY")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self.model = model

    @staticmethod
    def build_empty_digest(digest_date: str, stats: dict[str, object] | None = None) -> DailyDigest:
        stats = stats or {}
        total_candidates = int(stats.get("total_candidates", 0))
        total_kept = int(stats.get("total_kept", 0))
        window_text = str(stats.get("window_text", "北京时间过去 24 小时"))
        return DailyDigest(
            subject=f"AI 前沿日报｜{digest_date}",
            opening_summary=(
                f"- 本期抓取范围：北京时间 {window_text}。\n"
                f"- 今日共抓取候选链接 {total_candidates} 条，"
                f"成功提取正文 {total_kept} 篇，最终精选 0 条进入日报。\n"
                "- 今日要点一：指定来源暂未形成可用于日报的新增内容。\n"
                "- 今日要点二：部分来源可能无更新、被时间窗口过滤或抓取失败。\n"
                "- 今日值得继续关注：后续来源更新及可验证的 AI 产品、研究和商业化进展。"
            ),
            trend="",
            items=[],
        )

    def build_digest(
        self,
        articles: list[Article],
        digest_date: str,
        stats: dict[str, object] | None = None,
        max_items: int = 5,
        max_per_source: int = 2,
    ) -> DailyDigest:
        stats = stats or {}
        if not articles:
            return self.build_empty_digest(digest_date, stats)

        payload = self._article_payload(articles)
        selected_ids = self._rerank_articles(payload, max_items=max_items, max_per_source=max_per_source)
        payload_by_id = {item["id"]: item for item in payload}
        selected_payload = [payload_by_id[article_id] for article_id in selected_ids if article_id in payload_by_id]
        if not selected_payload:
            selected_payload = payload[:max_items]
        prompt_stats = {
            **stats,
            "final_selected": len(selected_payload),
        }

        prompt = f"""
你是一名中文 AI 科技行业分析师，请基于给定文章生成一封中文 AI 前沿日报。

总体风格：
- 专业、克制、可信，像科技行业分析师写的简报。
- 不使用夸张词、营销词、标题党词。
- 禁止使用这些词或近似表达：{", ".join(BANNED_STYLE_WORDS)}。
- 除非原文是正式法律判决或官方表述，不使用“认罪”“实锤”等定性词。
- 对争议性或单一媒体报道内容，使用“据报道”“报道称”“该文提到”“尚待进一步确认”等谨慎措辞。
- 不把单一媒体报道写成确定事实。
- 不使用“证明”“重塑”“必然”“决定性”等绝对化结论，除非原文有充分依据。
- 不编造背景，不扩展原文没有的信息。

硬性要求：
- 只基于输入文章，不要编造。
- 不要改变邮件结构和字段。
- 英文文章只使用已提供的中文编辑材料继续总结，不要全文翻译。
- 中文文章只总结。
- 最多输出 {max_items} 条，优先输出 3-{max_items} 条。
- 同一来源最多 {max_per_source} 条；如果其他来源有足够高质量文章，优先保留不同来源。
- 同一事件只保留信息量最高的一篇。
- importance 只能是“高”“中”“低”。

开头摘要写法：
- opening_summary 必须写成 5 条短 bullet，每条以“- ”开头。
- 第 1 条必须严格包含：本期抓取范围：北京时间 YYYY-MM-DD HH:mm 至 YYYY-MM-DD HH:mm。
- 第 2 条必须严格包含：今日共抓取候选链接 X 条，成功提取正文 Y 篇，最终精选 Z 条进入日报。
- 第 3 条必须以“今日要点一：”开头，归纳入选文章背后的共同趋势。
- 第 4 条必须以“今日要点二：”开头，归纳另一个行业方向或结构性变化。
- 第 5 条必须以“今日值得继续关注：”开头。
- 每条不超过 80 个中文字符；每条只表达一个判断。
- 不要使用“今日主要变化”这个说法。
- 不要逐条复述入选文章标题，不要堆砌公司名和新闻标题。
- 每条回答：今天 AI 行业在什么方向出现了值得关注的信息。
- 可归纳为公司治理、商业化、算力效率、AI agent、国产芯片、模型架构等方向。
- 不要写“引发争议”“凸显挑战”等泛化表达，除非说明具体争议或挑战。
- 只基于当天抓到的内容，不做空泛预测。
- trend 输出空字符串 ""，不要另写一段趋势。

今日最重要的 3-5 条写法：
- 标题中性、准确，不要标题党。
- title 不超过 28 个中文字符，更像专业简报标题。
- title 不要直接堆砌完整事实；详细事实放到 core_fact。
- core_fact 字段对应邮件里的“核心事实”，不超过 80 个中文字符。
- core_fact 只写可从原文确认的事实，回答谁、做了什么、涉及什么产品/公司/技术/事件。
- core_fact 不写评价、预测、情绪化表达。
- core_fact 必须是完整中文句子，不要使用省略号。
- key_points 最多 3 条，每条不超过 50 个中文字符。
- key_points 必须是完整短句，不要使用省略号。
- important_meaning 字段对应邮件里的“重要意义”。
- important_meaning 必须回答：影响谁？影响什么？后续应关注什么？
- important_meaning 必须是完整中文句子，不要使用省略号。
- 如果信息来自单一媒体报道，core_fact 或 important_meaning 中必须加“据报道”或“该文称”。
- key_points 只写高信息密度内容，不要重复 core_fact。
- 每条都必须保留原文链接。
- 所有字段都必须写成完整句子，不能截断词语、公司名或产品名。
- 不要使用“...”或“…”。

输出必须是 JSON，不要 Markdown，不要解释。

JSON schema：
{{
  "subject": "AI 前沿日报｜{digest_date}",
  "opening_summary": "- 本期抓取范围：北京时间 YYYY-MM-DD HH:mm 至 YYYY-MM-DD HH:mm。\\n- 今日共抓取候选链接 X 条，成功提取正文 Y 篇，最终精选 Z 条进入日报。\\n- 今日要点一：...\\n- 今日要点二：...\\n- 今日值得继续关注：...",
  "trend": "",
  "items": [
    {{
      "importance": "高",
      "title": "...",
      "source": "...",
      "url": "...",
      "core_fact": "...",
      "key_points": ["...", "...", "..."],
      "important_meaning": "..."
    }}
  ]
}}

抓取统计：
{json.dumps(prompt_stats, ensure_ascii=False)}

已通过 rerank 入选的文章：
{json.dumps(selected_payload, ensure_ascii=False)}
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.15,
            response_format={"type": "json_object"},
        )
        digest = self._parse_digest(response.choices[0].message.content or "{}")
        return self._quality_check_and_rewrite(
            digest,
            stats=prompt_stats,
            max_items=max_items,
            max_per_source=max_per_source,
        )

    def _article_payload(self, articles: list[Article]) -> list[dict[str, object]]:
        return [
            {
                "id": f"a{idx}",
                "title": a.title,
                "source": a.source_name,
                "url": a.url,
                "language": a.source_language.value,
                "strategy": a.source_strategy,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "content": a.content[:8000],
            }
            for idx, a in enumerate(articles, start=1)
        ]

    def _rerank_articles(self, payload: list[dict[str, object]], max_items: int, max_per_source: int) -> list[str]:
        prompt = f"""
你是中文 AI 科技日报的选题编辑。请对候选文章做 rerank，不要生成日报。

评分标准：
1. 行业影响：是否影响大模型、AI agent、算力、商业化、监管、头部公司竞争格局。
2. 信息增量：是否提供新数据、新产品、新融资、新政策、新研究。
3. 可信度：官方来源、权威媒体、原始信息优先。
4. 与 AI 主题相关度：弱相关科技新闻降权。
5. 重复度：同一事件只保留信息量最高的一篇。
6. 来源多样性：最终 Top {max_items} 同一来源最多 {max_per_source} 条；其他来源有足够高质量文章时，优先不同来源。

要求：
- 返回按优先级排序的候选列表，最多 10 条。
- 每条包含 id、score、source、reason。
- 不要选择营销化、标题党、弱 AI 相关内容。
- 不要只按抓取顺序。
- 输出 JSON，不要解释。

JSON schema：
{{
  "ranked": [
    {{"id": "a1", "score": 92, "source": "xxx", "reason": "一句话说明入选理由"}}
  ]
}}

候选文章：
{json.dumps(payload, ensure_ascii=False)}
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        try:
            data = json.loads(response.choices[0].message.content or "{}")
            ranked = data.get("ranked", [])
        except json.JSONDecodeError:
            ranked = []

        source_by_id = {str(item["id"]): str(item["source"]) for item in payload}
        selected: list[str] = []
        source_counts: dict[str, int] = {}

        for item in ranked:
            article_id = str(item.get("id", ""))
            source = source_by_id.get(article_id)
            if not source or article_id in selected:
                continue
            if source_counts.get(source, 0) >= max_per_source:
                continue
            selected.append(article_id)
            source_counts[source] = source_counts.get(source, 0) + 1
            if len(selected) >= max_items:
                break

        if len(selected) < 3:
            for item in payload:
                article_id = str(item["id"])
                source = str(item["source"])
                if article_id in selected or source_counts.get(source, 0) >= max_per_source:
                    continue
                selected.append(article_id)
                source_counts[source] = source_counts.get(source, 0) + 1
                if len(selected) >= max_items:
                    break
        return selected

    def _quality_check_and_rewrite(
        self,
        digest: DailyDigest,
        stats: dict[str, object],
        max_items: int,
        max_per_source: int,
    ) -> DailyDigest:
        prompt = f"""
请对下面这封 AI 前沿日报做最终质量自检，并在必要时直接改写为合格版本。

自检项：
1. 是否还出现“一句话概括”；如果有，全部改为 core_fact。
2. 开头摘要是否包含抓取数量。
3. 开头摘要是否使用“今日要点”，而不是“今日主要变化”。
4. 开头摘要是否只是新闻标题拼接；如果是，需要改成高层编辑判断。
5. 是否有标题党、营销化或夸张词汇：{", ".join(BANNED_STYLE_WORDS)}。
6. 是否有未经证实的绝对判断，尤其是单一媒体报道被写成确定事实。
7. 是否有单一来源占比过高；同一来源最多 {max_per_source} 条。
8. 是否每条都保留原文链接。
9. 是否适合手机阅读：title 不超过 28 字，core_fact 不超过 80 字，key_points 每条不超过 50 字。
10. “重要意义”是否回答：影响谁、影响什么、后续关注什么。
11. 是否出现“...”或“…”；如果有，必须改成完整句子。
12. 是否存在被截断的词语、公司名或产品名；如果有，必须重写为完整短句。

改写规则：
- 保持邮件标题格式：AI 前沿日报｜YYYY-MM-DD。
- 保持原 JSON 字段，不要增加字段。
- 不改变来源、链接，不编造信息。
- 对单一媒体报道使用“据报道”“报道称”“该文称”“尚待进一步确认”等措辞。
- opening_summary 必须是 5 条 bullet，依次为抓取范围、抓取统计、今日要点一、今日要点二、今日值得继续关注。
- 今日要点必须总结共同趋势，不要复述文章标题。
- trend 必须输出空字符串 ""。
- items 最多 {max_items} 条，同一来源最多 {max_per_source} 条。
- title 不超过 28 个中文字符。
- core_fact、important_meaning、key_points 必须是完整句子，禁止省略号。
- 输出修正后的 JSON，不要解释。

抓取统计：
{json.dumps(stats, ensure_ascii=False)}

日报 JSON：
{json.dumps(digest.model_dump(mode="json"), ensure_ascii=False, indent=2)}
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.05,
            response_format={"type": "json_object"},
        )
        checked = self._parse_digest(response.choices[0].message.content or "{}")
        return self._enforce_basic_quality(
            checked,
            stats=stats,
            max_items=max_items,
            max_per_source=max_per_source,
        )

    def _enforce_basic_quality(
        self,
        digest: DailyDigest,
        stats: dict[str, object],
        max_items: int,
        max_per_source: int,
    ) -> DailyDigest:
        items = []
        source_counts: dict[str, int] = {}
        for item in digest.items:
            if source_counts.get(item.source, 0) >= max_per_source:
                continue
            source_counts[item.source] = source_counts.get(item.source, 0) + 1
            clean_points = [self._trim_text(point, 50) for point in item.key_points[:3]]
            items.append(
                item.model_copy(
                    update={
                        "title": self._trim_text(self._remove_banned_words(item.title), 28),
                        "core_fact": self._trim_text(self._remove_banned_words(item.core_fact), 80),
                        "key_points": clean_points,
                        "important_meaning": self._trim_text(self._remove_banned_words(item.important_meaning), 120),
                    }
                )
            )
        final_items = items[:max_items]
        opening_summary = self._normalize_opening_summary(digest.opening_summary, stats, len(final_items))
        return digest.model_copy(update={"opening_summary": opening_summary, "trend": "", "items": final_items})

    def _normalize_opening_summary(
        self,
        opening_summary: str,
        stats: dict[str, object],
        final_selected: int,
    ) -> str:
        total_candidates = int(stats.get("total_candidates", 0))
        total_kept = int(stats.get("total_kept", 0))
        window_text = str(stats.get("window_text", "北京时间过去 24 小时"))
        window_line = f"- 本期抓取范围：北京时间 {window_text}。"
        count_line = (
            f"- 今日共抓取候选链接 {total_candidates} 条，"
            f"成功提取正文 {total_kept} 篇，最终精选 {final_selected} 条进入日报。"
        )
        lines = [line.strip() for line in opening_summary.splitlines() if line.strip().startswith("-")]
        useful_lines = [
            self._trim_text(self._remove_banned_words(line), 80)
            for line in lines
            if "本期抓取范围" not in line
            and "今日共抓取候选链接" not in line
            and "今日主要变化" not in line
        ]
        point_one = next((line for line in useful_lines if "今日要点一：" in line), "- 今日要点一：今日内容集中在 AI 产品、商业化或产业进展。")
        point_two = next((line for line in useful_lines if "今日要点二：" in line), "- 今日要点二：入选事项仍需结合后续官方信息和市场反馈观察。")
        follow = next((line for line in useful_lines if "今日值得继续关注：" in line), "- 今日值得继续关注：AI 应用落地、算力供给与头部公司竞争变化。")
        return "\n".join(
            [
                self._trim_text(window_line, 80),
                count_line,
                self._trim_text(point_one, 80),
                self._trim_text(point_two, 80),
                self._trim_text(follow, 80),
            ]
        )

    @staticmethod
    def _parse_digest(raw_content: str) -> DailyDigest:
        data = json.loads(raw_content or "{}")
        for item in data.get("items", []):
            if "core_fact" not in item:
                if "summary" in item:
                    item["core_fact"] = item["summary"]
                elif "核心事实" in item:
                    item["core_fact"] = item["核心事实"]
            if "important_meaning" not in item and "重要意义" in item:
                item["important_meaning"] = item["重要意义"]
        return DailyDigest.model_validate(data)

    @staticmethod
    def _remove_banned_words(text: str) -> str:
        cleaned = text
        for word in BANNED_STYLE_WORDS:
            cleaned = cleaned.replace(word, "")
        return cleaned.replace("...", "").replace("…", "").strip()

    @staticmethod
    def _trim_text(text: str, max_chars: int) -> str:
        text = text.strip()
        if len(text) <= max_chars:
            return text

        sentence_endings = [match.end() for match in re.finditer(r"[。！？；.!?;]", text) if match.end() <= max_chars]
        if sentence_endings:
            return text[: sentence_endings[-1]].strip()

        soft_breaks = [text.rfind(mark, 0, max_chars + 1) for mark in ("，", "、", "：", "；", ",", " ")]
        soft_break = max(soft_breaks)
        if soft_break >= max_chars // 2:
            return text[:soft_break].rstrip("，、：；, ")

        return text
