from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.crawler import NewsCrawler
from src.emailer import render_email_text, send_email
from src.models import Article
from src.storage import SeenStore
from src.summarizer import NewsSummarizer
from src.translator import OpenAITranslator
from src.utils import load_config, setup_logging

logger = logging.getLogger(__name__)


def render_diagnostics_text(source_stats: list[dict[str, object]]) -> str:
    lines = ["", "----", "", "噪声过滤说明 / 抓取诊断"]
    for stat in source_stats:
        lines.append(
            (
                f"- {stat.get('source_name') or stat.get('source')} "
                f"({stat.get('source_type')}): "
                f"discovered={stat.get('discovered_count')}, "
                f"discovery_methods={stat.get('discovery_methods')}, "
                f"fetched={stat.get('fetched_count')}, "
                f"kept={stat.get('kept_count')}, "
                f"partial={stat.get('partial_count')}, "
                f"filtered_by_time={stat.get('filtered_by_time_count')}, "
                f"filtered_by_relevance={stat.get('filtered_by_relevance_count')}, "
                f"body_failed={stat.get('body_failed_count')}, "
                f"discovery_failed={stat.get('discovery_failed_count')}, "
                f"gdelt_fallback={stat.get('gdelt_fallback_count')}, "
                f"gdelt_status={stat.get('gdelt_status')}, "
                f"gdelt_error={stat.get('gdelt_error_message') or ''}, "
                f"premium_limited={stat.get('premium_limited_count')}, "
                f"list_page_only={stat.get('list_page_only_count')}, "
                f"filtered_by_url={stat.get('filtered_by_url_count')}, "
                f"failed={stat.get('failed_count')}, "
                f"status={stat.get('status')}, "
                f"error={stat.get('error_message') or ''}"
            )
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI news daily digest agent")
    parser.add_argument("--config", default="config/sources.yaml", help="Path to sources YAML")
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=int(os.getenv("LOOKBACK_HOURS", "24")),
        help="Fetch articles published within the last N hours. Default: 24. Example for testing: 720.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print email content without sending")
    parser.add_argument("--no-cache", action="store_true", help="Do not use or update seen article cache")
    parser.add_argument("--max-items", type=int, default=5, help="Maximum number of news items in the digest")
    parser.add_argument("--max-per-source", type=int, default=2, help="Maximum selected items per source")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    setup_logging()
    args = parse_args()
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    start_time = now - timedelta(hours=args.lookback_hours)
    end_time = now
    window_text = f"{start_time.strftime('%Y-%m-%d %H:%M')} 至 {end_time.strftime('%Y-%m-%d %H:%M')}"

    config = load_config(args.config)
    crawler = NewsCrawler(config=config)
    store = None if args.no_cache else SeenStore()
    all_articles: list[Article] = []
    source_stats: list[dict[str, object]] = []

    try:
        for source in config.sources:
            articles, result = crawler.crawl_source(source, start_time, end_time)
            stat = {
                "source": result.source_name,
                "source_name": result.source_name,
                "source_type": result.source_type,
                "candidates": result.candidates,
                "discovered_count": result.candidates,
                "fetched": result.fetched,
                "fetched_count": result.fetched,
                "discovery_methods": result.discovery_methods,
                "kept": result.kept,
                "kept_count": result.kept,
                "partial_count": result.partial_count,
                "body_failed_count": result.body_failed_count,
                "discovery_failed_count": result.discovery_failed_count,
                "premium_limited_count": result.premium_limited_count,
                "gdelt_fallback_count": result.gdelt_fallback_count,
                "gdelt_status": result.gdelt_status,
                "gdelt_error_message": result.gdelt_error_message,
                "list_page_only_count": result.list_page_only_count,
                "filtered": len(result.filtered),
                "filtered_by_time_count": result.filtered_by_time_count,
                "filtered_by_relevance_count": result.filtered_by_relevance_count,
                "filtered_by_url_count": result.filtered_by_url_count,
                "failed_count": result.failed_count,
                "status": result.status,
                "error_type": result.error_type,
                "error_message": result.error,
            }
            if result.failed:
                if result.error and "DNS 解析失败或网络不可达" in result.error:
                    logger.warning("%s failed/skipped: %s", result.source_name, result.error)
                else:
                    logger.error("%s failed/skipped: %s", result.source_name, result.error)
                source_stats.append(stat)
                continue

            kept_for_digest: list[Article] = []
            for article in articles:
                if store and store.has_seen_article(article):
                    result.filtered.append(f"历史重复：{article.title}")
                    continue
                kept_for_digest.append(article)

            logger.info(
                "%s summary: candidates=%s fetched=%s kept=%s digest_kept=%s filtered=%s",
                result.source_name,
                result.candidates,
                result.fetched,
                result.kept,
                len(kept_for_digest),
                len(result.filtered),
            )
            for reason in result.filtered:
                logger.info("%s filtered: %s", result.source_name, reason)
            all_articles.extend(kept_for_digest)
            stat["filtered"] = len(result.filtered)
            stat["kept"] = len(kept_for_digest)
            stat["kept_count"] = len(kept_for_digest)
            source_stats.append(stat)
    finally:
        crawler.close()

    run_stats = {
        "total_candidates": sum(int(stat["candidates"]) for stat in source_stats),
        "total_fetched": sum(int(stat["fetched"]) for stat in source_stats),
        "total_kept": sum(int(stat["kept"]) for stat in source_stats),
        "total_partial": sum(int(stat.get("partial_count", 0)) for stat in source_stats),
        "final_selected": 0,
        "window_text": window_text,
        "lookback_hours": args.lookback_hours,
        "source_stats": source_stats,
    }

    all_articles.sort(
        key=lambda article: (
            -article.investment_score,
            article.is_partial,
            article.content_source in {"gdelt", "list_page"},
            article.time_status not in {"published_within_window", "known"},
            -(article.published_at.timestamp() if article.published_at else 0),
        )
    )

    prepared_articles: list[Article] = []
    if all_articles:
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        translator = OpenAITranslator(model=model) if any(
            article.source_language.value == "en" for article in all_articles
        ) else None
        for article in all_articles:
            if article.source_language.value == "en" and translator:
                try:
                    material = translator.translate_key_parts(article.title, article.content)
                    article = article.model_copy(update={"content": material})
                except Exception as exc:
                    logger.warning("English translation step failed for %s: %s", article.url, exc)
            prepared_articles.append(article)
        digest = NewsSummarizer(model=model).build_digest(
            prepared_articles,
            today,
            stats=run_stats,
            max_items=args.max_items,
            max_per_source=args.max_per_source,
        )
    else:
        digest = NewsSummarizer.build_empty_digest(today, run_stats)
    body = render_email_text(digest)
    body = f"{body}\n{render_diagnostics_text(source_stats)}"

    if args.dry_run:
        print(body)
    else:
        send_email(digest.subject, body)
        logger.info("Email sent: %s", digest.subject)

    if store and all_articles and not args.dry_run:
        store.add_articles(all_articles)


if __name__ == "__main__":
    main()
