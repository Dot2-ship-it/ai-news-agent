from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

from src.crawler import NewsCrawler
from src.emailer import render_email_text, send_email
from src.models import Article
from src.storage import SeenStore
from src.summarizer import NewsSummarizer
from src.translator import OpenAITranslator
from src.utils import load_config, setup_logging

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI news daily digest agent")
    parser.add_argument("--config", default="config/sources.yaml", help="Path to sources YAML")
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=int(os.getenv("LOOKBACK_HOURS", "48")),
        help="Fetch articles published within the last N hours. Default: 48. Example for testing: 720.",
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

    config = load_config(args.config)
    crawler = NewsCrawler()
    store = None if args.no_cache else SeenStore()
    all_articles: list[Article] = []
    source_stats: list[dict[str, int | str]] = []

    try:
        for source in config.sources:
            articles, result = crawler.crawl_source(source, args.lookback_hours)
            stat = {
                "source": result.source_name,
                "candidates": result.candidates,
                "fetched": result.fetched,
                "kept": result.kept,
                "filtered": len(result.filtered),
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
            source_stats.append(stat)
    finally:
        crawler.close()

    run_stats = {
        "total_candidates": sum(int(stat["candidates"]) for stat in source_stats),
        "total_fetched": sum(int(stat["fetched"]) for stat in source_stats),
        "total_kept": sum(int(stat["kept"]) for stat in source_stats),
        "final_selected": 0,
        "source_stats": source_stats,
    }

    prepared_articles: list[Article] = []
    digest_date = datetime.now().date()
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
            digest_date,
            stats=run_stats,
            max_items=args.max_items,
            max_per_source=args.max_per_source,
        )
    else:
        digest = NewsSummarizer.build_empty_digest(digest_date, run_stats)
    body = render_email_text(digest)

    if args.dry_run:
        print(body)
    else:
        send_email(digest.subject, body)
        logger.info("Email sent: %s", digest.subject)

    if store and all_articles and not args.dry_run:
        store.add_articles(all_articles)


if __name__ == "__main__":
    main()
