from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.crawler import NewsCrawler
from src.emailer import render_email_html, render_email_subject, render_email_text, send_email
from src.event_aggregator import build_event_bundle
from src.models import Article, DailyDigest
from src.storage import SeenStore
from src.summarizer import NewsSummarizer
from src.translator import OpenAITranslator
from src.utils import load_config, setup_logging

logger = logging.getLogger(__name__)
ARTIFACTS_DIR = Path("artifacts")


def render_diagnostics_text(source_stats: list[dict[str, object]]) -> str:
    success_sources = [
        str(stat.get("source_name") or stat.get("source"))
        for stat in source_stats
        if stat.get("status") == "success"
    ]
    partial_sources = [
        str(stat.get("source_name") or stat.get("source"))
        for stat in source_stats
        if stat.get("status") == "partial_success"
    ]
    failed_sources = [
        str(stat.get("source_name") or stat.get("source"))
        for stat in source_stats
        if stat.get("status") in {"fetch_failed", "body_unavailable"}
    ]
    failure_reasons: dict[str, int] = {}
    for stat in source_stats:
        reason = str(stat.get("error_type") or stat.get("status") or "unknown")
        if stat.get("gdelt_status") == "rate_limited":
            reason = "gdelt_rate_limited"
        if stat.get("status") in {"fetch_failed", "partial_success", "body_unavailable"}:
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
    main_reasons = sorted(failure_reasons.items(), key=lambda item: (-item[1], item[0]))[:3]
    lines = [
        "",
        "抓取诊断",
        f"- 成功源：{_join_source_names(success_sources)}",
        f"- 部分成功源：{_join_source_names(partial_sources)}",
        f"- 失败源：{_join_source_names(failed_sources)}",
        f"- 主要失败原因：{', '.join(f'{reason}({count})' for reason, count in main_reasons) if main_reasons else '无明显失败聚类'}",
    ]
    return "\n".join(lines)


def _join_source_names(names: list[str]) -> str:
    if not names:
        return "无"
    if len(names) <= 6:
        return "、".join(names)
    return "、".join(names[:6]) + f" 等 {len(names)} 个"


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
    parser.add_argument("--preview-email", action="store_true", help="Render a local sample email without fetching or sending")
    return parser.parse_args()


def render_preview_email() -> str:
    fixture_path = Path("tests/fixtures/sample_digest_events.json")
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    digest = DailyDigest.model_validate(data["digest"])
    source_stats = data.get("source_stats", [])
    body = f"{render_email_text(digest, source_stats=source_stats)}\n{render_diagnostics_text(source_stats)}"
    html_body = render_email_html(digest, source_stats=source_stats)
    write_artifacts(body, html_body, digest, source_stats)
    return body


def write_artifacts(
    body: str,
    html_body: str,
    digest: DailyDigest,
    source_stats: list[dict[str, object]],
) -> dict[str, str]:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    preview_path = ARTIFACTS_DIR / "preview_email.txt"
    html_preview_path = ARTIFACTS_DIR / "preview_email.html"
    diagnostics_path = ARTIFACTS_DIR / "crawl_diagnostics.json"
    events_path = ARTIFACTS_DIR / "digest_events.json"
    preview_path.write_text(body, encoding="utf-8")
    html_preview_path.write_text(html_body, encoding="utf-8")
    diagnostics_path.write_text(json.dumps(source_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    events_path.write_text(
        json.dumps(asdict(build_event_bundle(digest)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "preview_email": str(preview_path),
        "preview_email_html": str(html_preview_path),
        "crawl_diagnostics": str(diagnostics_path),
        "digest_events": str(events_path),
    }


def main() -> None:
    load_dotenv()
    setup_logging()
    args = parse_args()
    if args.preview_email:
        print(render_preview_email())
        return
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
    subject = render_email_subject(digest)
    body = render_email_text(digest, source_stats=source_stats)
    body = f"{body}\n{render_diagnostics_text(source_stats)}"
    html_body = render_email_html(digest, source_stats=source_stats)
    write_artifacts(body, html_body, digest, source_stats)

    if args.dry_run:
        print(body)
    else:
        send_email(subject, body, html_body=html_body)
        logger.info("Email sent: %s", subject)

    if store and all_articles and not args.dry_run:
        store.add_articles(all_articles)


if __name__ == "__main__":
    main()
