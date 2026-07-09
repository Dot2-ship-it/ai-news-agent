from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

from openai import OpenAI

from .investment_filter import derive_report_fields
from .models import Article, DailyDigest, WatchItem


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

WATCHLIST_THRESHOLD = 45
MAIN_STORY_THRESHOLD = 80
OBSERVATION_POOL_BLOCKLIST = (
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
    "products",
)


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
        total_fetched = int(stats.get("total_fetched", 0))
        total_kept = int(stats.get("total_kept", 0))
        window_text = str(stats.get("window_text", "北京时间过去 24 小时"))
        return DailyDigest(
            subject=f"AI 投研情报日报｜{digest_date}",
            opening_summary=(
                f"- 本期抓取范围：北京时间 {window_text}。\n"
                f"- 今日共抓取候选链接 {total_candidates} 条，"
                f"成功提取正文 {total_fetched} 篇，通过投研过滤保留 {total_kept} 篇，最终精选 0 条进入日报。\n"
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
你是一名中文 AI 科技投研分析师，请基于给定文章生成一封中文 AI 投研情报日报。

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
- 日报必须按投研主题组织，不要按来源堆叠。
- 优先展示 investment_score 高、投研信号强、来源质量高的文章。
- 默认排除技术教程、论文、模型测评、开源项目、Prompt、工具合集、产品体验和活动宣传。
- 不要改变邮件结构和字段。
- 英文文章只使用已提供的中文编辑材料继续总结，不要全文翻译。
- 中文文章只总结。
- 最多输出 {max_items} 条，优先输出 3-{max_items} 条。
- 同一来源最多 {max_per_source} 条；如果其他来源有足够高质量文章，优先保留不同来源。
- 同一事件只保留信息量最高的一篇。
- time_status 为 time_unknown 或 unknown 的文章如果入选，core_fact 或 important_meaning 必须注明“发布时间缺失，未严格纳入 24 小时窗口”。
- 每条 item 必须填写 topic，且只能使用这些主题之一：核心信号、AI Capex / 数据中心、算力与半导体供应链、AI 公司与商业化、二级市场相关、中国 AI 产业链。
- importance 只能是“高”“中”“低”。
- is_partial 为 true 或 body_status 为 body_unavailable 的文章可进入日报，但必须降权；不能压过 SEC、IR 或有完整正文的高质量内容。
- premium_limited 只能说明“订阅限制，仅基于标题/列表页信息”，不要暗示已经读取全文。
- partial 文章入选时，content_status 必须填写简短说明。
- 每条 item 保留 discovery_method，用于标记 list_page、rss、sitemap、search_index、gdelt、sec_api 或 ir_rss。
- 每条 item 尽量保留 published_at、time_status、investment_score、is_partial；这些字段用于后续投研结构化渲染。

开头摘要写法：
- opening_summary 必须写成 5 条短 bullet，每条以“- ”开头。
- 第 1 条必须严格包含：本期抓取范围：北京时间 YYYY-MM-DD HH:mm 至 YYYY-MM-DD HH:mm。
- 第 2 条必须严格包含：今日共抓取候选链接 X 条，成功提取正文 Y 篇，通过投研过滤保留 K 篇，最终精选 Z 条进入日报。
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

投研情报条目写法：
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
	  "subject": "AI 投研情报日报｜{digest_date}",
  "opening_summary": "- 本期抓取范围：北京时间 YYYY-MM-DD HH:mm 至 YYYY-MM-DD HH:mm。\\n- 今日共抓取候选链接 X 条，成功提取正文 Y 篇，通过投研过滤保留 K 篇，最终精选 Z 条进入日报。\\n- 今日要点一：...\\n- 今日要点二：...\\n- 今日值得继续关注：...",
  "trend": "",
  "items": [
    {{
      "importance": "高",
      "topic": "核心信号",
      "title": "...",
      "source": "...",
	      "url": "...",
	      "core_fact": "...",
	      "key_points": ["...", "...", "..."],
	      "important_meaning": "...",
	      "content_status": null,
	      "discovery_method": "list_page",
	      "published_at": null,
	      "time_status": "published_within_window",
	      "investment_score": 80,
	      "is_partial": false
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
        checked = self._quality_check_and_rewrite(
            digest,
            stats=prompt_stats,
            max_items=max_items,
            max_per_source=max_per_source,
        )
        return self._enrich_digest(checked, articles)

    def _article_payload(self, articles: list[Article]) -> list[dict[str, object]]:
        return [
            {
                "id": f"a{idx}",
                "source_id": a.source_id,
                "title": a.title,
                "source": a.source_name,
                "url": a.url,
                "language": a.source_language.value,
                "strategy": a.source_strategy,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "time_status": a.time_status,
                "time_note": "发布时间缺失，未严格纳入 24 小时窗口" if a.time_status in {"time_unknown", "unknown"} else "",
                "source_type": a.source_type,
                "quality_tier": a.quality_tier,
                "investment_score": a.investment_score,
                "matched_companies": a.matched_companies,
                "matched_signals": a.matched_signals,
                "topic": a.topic,
                "body_status": a.body_status,
                "discovery_status": a.discovery_status,
                "content_source": a.content_source,
                "discovery_method": a.discovery_method,
                "is_partial": a.is_partial,
                "partial_reason": a.partial_reason,
                "content_status": self._content_status(a),
                "content": a.content[:8000],
            }
            for idx, a in enumerate(articles, start=1)
        ]

    def _rerank_articles(self, payload: list[dict[str, object]], max_items: int, max_per_source: int) -> list[str]:
        prompt = f"""
你是中文 AI 投研日报的选题编辑。请对候选文章做 rerank，不要生成日报。

评分标准：
1. 投资影响：是否涉及 capex、收入、毛利率、业绩指引、订单、客户、合同、供需、监管、出口管制、融资、估值或市场反应。
2. 产业链影响：是否影响 AI 数据中心、电力、冷却、GPU、半导体供应链、云厂商或重点 AI 公司。
3. 可信度：官方 IR、SEC、Reuters、SemiAnalysis 等一手或高质量来源优先。
4. 主题相关度：没有公司、财务、产业链或市场信号的泛 AI 内容降权。
5. 噪声控制：技术教程、论文、模型测评、开源项目、Prompt、工具合集、活动宣传不要选择。
6. 来源多样性：最终 Top {max_items} 同一来源最多 {max_per_source} 条；其他来源有足够高质量文章时，优先不同来源。
7. 排序参考：优先使用 investment_score 高的文章。
8. partial、GDELT fallback、premium_limited 文章只能补充信息，不能排在 SEC、IR、完整正文的高质量文章前面。
9. SemiAnalysis 主日报最多 1 条；LatePost 主日报最多 2 条。

要求：
- 返回按优先级排序的候选列表，最多 10 条。
- 每条包含 id、score、source、reason。
- 不要选择营销化、标题党、弱 AI 相关内容。
- premium_limited 只能按标题/列表页判断，不要假设已读取全文。
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
        source_id_by_id = {str(item["id"]): str(item.get("source_id", "")) for item in payload}
        selected: list[str] = []
        source_counts: dict[str, int] = {}
        source_id_counts: dict[str, int] = {}

        for item in ranked:
            article_id = str(item.get("id", ""))
            source = source_by_id.get(article_id)
            if not source or article_id in selected:
                continue
            if source_counts.get(source, 0) >= max_per_source:
                continue
            source_id = source_id_by_id.get(article_id, "")
            if self._source_specific_limit_reached(source_id, source_id_counts):
                continue
            selected.append(article_id)
            source_counts[source] = source_counts.get(source, 0) + 1
            source_id_counts[source_id] = source_id_counts.get(source_id, 0) + 1
            if len(selected) >= max_items:
                break

        if len(selected) < 3:
            for item in payload:
                article_id = str(item["id"])
                source = str(item["source"])
                if article_id in selected or source_counts.get(source, 0) >= max_per_source:
                    continue
                source_id = source_id_by_id.get(article_id, "")
                if self._source_specific_limit_reached(source_id, source_id_counts):
                    continue
                selected.append(article_id)
                source_counts[source] = source_counts.get(source, 0) + 1
                source_id_counts[source_id] = source_id_counts.get(source_id, 0) + 1
                if len(selected) >= max_items:
                    break
        return selected

    @staticmethod
    def _source_specific_limit_reached(source_id: str, source_id_counts: dict[str, int]) -> bool:
        limits = {
            "semianalysis": 1,
            "latepost": 2,
        }
        limit = limits.get(source_id)
        return limit is not None and source_id_counts.get(source_id, 0) >= limit

    @staticmethod
    def _source_name_specific_limit_reached(source_name: str, source_counts: dict[str, int]) -> bool:
        if source_name == "SemiAnalysis":
            return source_counts.get(source_name, 0) >= 1
        if "LatePost" in source_name or "晚点" in source_name:
            return source_counts.get(source_name, 0) >= 2
        return False

    @staticmethod
    def _content_status(article: Article) -> str | None:
        if not article.is_partial:
            return None
        if article.partial_reason == "premium_limited":
            return "订阅限制，仅基于标题/列表页信息"
        if article.content_source == "gdelt":
            return "GDELT fallback 发现，正文不可用"
        if article.body_status == "body_unavailable":
            return "正文不可用，仅基于标题/列表页信息"
        return "部分内容可用"

    def _enrich_digest(self, digest: DailyDigest, articles: list[Article]) -> DailyDigest:
        article_by_url = {article.url: article for article in articles}
        enriched_items = []
        selected_urls: set[str] = set()
        for item in digest.items:
            article = article_by_url.get(item.url)
            if article:
                selected_urls.add(article.url)
                content = "\n".join(
                    [
                        item.core_fact,
                        item.important_meaning,
                        "\n".join(item.key_points),
                        article.content[:1200],
                    ]
                )
                fields = derive_report_fields(
                    title=item.title,
                    content=content,
                    url=item.url,
                    company_matches=article.matched_companies,
                    signal_matches_=article.matched_signals,
                )
                enriched_items.append(
                    item.model_copy(
                        update={
                            **fields,
                            "published_at": article.published_at.isoformat() if article.published_at else None,
                            "time_status": article.time_status,
                            "investment_score": article.investment_score,
                            "is_partial": article.is_partial,
                            "content_status": item.content_status or self._content_status(article),
                            "discovery_method": item.discovery_method or article.discovery_method,
                        }
                    )
                )
            else:
                fields = derive_report_fields(
                    title=item.title,
                    content="\n".join([item.core_fact, item.important_meaning, "\n".join(item.key_points)]),
                    url=item.url,
                    company_matches=item.company_layer,
                )
                enriched_items.append(item.model_copy(update=fields))
        return digest.model_copy(
            update={
                "items": enriched_items,
                "watchlist": self._build_watchlist(articles, selected_urls),
            }
        )

    def _build_watchlist(self, articles: list[Article], selected_urls: set[str]) -> list[WatchItem]:
        watchlist: list[WatchItem] = []
        for article in articles:
            if article.url in selected_urls:
                continue
            if not self._is_observation_pool_candidate(article):
                continue
            has_unknown_time = article.time_status in {"time_unknown", "unknown"}
            should_watch = (
                WATCHLIST_THRESHOLD <= article.investment_score < MAIN_STORY_THRESHOLD
                or (article.is_partial and article.investment_score >= WATCHLIST_THRESHOLD)
                or (has_unknown_time and bool(article.matched_signals))
            )
            if not should_watch:
                continue
            fields = derive_report_fields(
                title=article.title,
                content=article.content[:1600],
                url=article.url,
                company_matches=article.matched_companies,
                signal_matches_=article.matched_signals,
            )
            status_parts = []
            if article.is_partial:
                status_parts.append("partial")
            if has_unknown_time:
                status_parts.append("time_unknown")
            if article.content_source in {"gdelt", "list_page"}:
                status_parts.append(article.content_source)
            watchlist.append(
                WatchItem(
                    title=article.title,
                    url=article.url,
                    source=article.source_name,
                    industry_layer=str(fields["industry_layer"]),
                    company_layer=list(fields["company_layer"]),
                    direct_companies=list(fields["direct_companies"]),
                    inferred_companies=list(fields["inferred_companies"]),
                    watch_companies=list(fields["watch_companies"]),
                    signal_type=str(fields["signal_type"]),
                    score=article.investment_score,
                    status=" / ".join(status_parts) or "watch",
                    watch_variables=list(fields["watch_variables"]),
                    discovery_method=article.discovery_method,
                )
            )
        watchlist.sort(key=lambda item: (-item.score, item.status, item.source))
        return watchlist[:10]

    @staticmethod
    def _is_observation_pool_candidate(article: Article) -> bool:
        parsed = urlparse(article.url)
        path = parsed.path.strip("/").lower()
        title = article.title.strip().lower()
        haystack = f"{title} {path}"
        if not path:
            return False
        if any(keyword in haystack for keyword in OBSERVATION_POOL_BLOCKLIST):
            return False
        if title in {"semianalysis", "core research", "data product", "chipbook", "events", "compliance policies"}:
            return False
        has_unknown_time = article.time_status in {"time_unknown", "unknown"}
        body_unavailable = article.body_status != "body_available" or article.is_partial
        if has_unknown_time and body_unavailable and article.content_source in {"list_page", "gdelt"}:
            return False
        return True

    def _quality_check_and_rewrite(
        self,
        digest: DailyDigest,
        stats: dict[str, object],
        max_items: int,
        max_per_source: int,
    ) -> DailyDigest:
        prompt = f"""
	请对下面这封 AI 投研情报日报做最终质量自检，并在必要时直接改写为合格版本。

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
	- 保持邮件标题格式：AI 投研情报日报｜YYYY-MM-DD。
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
            if self._source_name_specific_limit_reached(item.source, source_counts):
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
        subject_date = digest.subject.split("｜")[-1] if "｜" in digest.subject else ""
        subject = f"AI 投研情报日报｜{subject_date}" if subject_date else digest.subject.replace("AI 前沿日报", "AI 投研情报日报")
        return digest.model_copy(update={"subject": subject, "opening_summary": opening_summary, "trend": "", "items": final_items})

    def _normalize_opening_summary(
        self,
        opening_summary: str,
        stats: dict[str, object],
        final_selected: int,
    ) -> str:
        total_candidates = int(stats.get("total_candidates", 0))
        total_fetched = int(stats.get("total_fetched", 0))
        total_kept = int(stats.get("total_kept", 0))
        window_text = str(stats.get("window_text", "北京时间过去 24 小时"))
        window_line = f"- 本期抓取范围：北京时间 {window_text}。"
        count_line = (
            f"- 今日共抓取候选链接 {total_candidates} 条，"
            f"成功提取正文 {total_fetched} 篇，通过投研过滤保留 {total_kept} 篇，最终精选 {final_selected} 条进入日报。"
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
