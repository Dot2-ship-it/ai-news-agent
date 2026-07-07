from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler import NewsCrawler  # noqa: E402
from src.utils import load_config, setup_logging  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lightweight source discovery diagnostics")
    parser.add_argument("--config", default="config/sources.yaml")
    parser.add_argument("--sources", required=True, help="Comma-separated source ids")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--lookback-hours", type=int, default=48)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    wanted = {source_id.strip() for source_id in args.sources.split(",") if source_id.strip()}
    config = load_config(args.config)
    tz = ZoneInfo("Asia/Shanghai")
    end_time = datetime.now(tz)
    start_time = end_time - timedelta(hours=args.lookback_hours)

    crawler = NewsCrawler(config=config, timeout=12)
    try:
        for source in config.sources:
            if source.id not in wanted:
                continue
            source = source.model_copy(update={"max_links": args.max_candidates})
            articles, result = crawler.crawl_source(source, start_time, end_time)
            print(
                json.dumps(
                    {
                        "source": result.source_id,
                        "primary_status": result.status,
                        "discovery_method": ",".join(result.discovery_methods) or None,
                        "discovered_count": result.candidates,
                        "kept_count": result.kept,
                        "body_failed_count": result.body_failed_count,
                        "gdelt_status": result.gdelt_status,
                        "gdelt_error_message": result.gdelt_error_message,
                        "error_message": result.error,
                        "items": [
                            {
                                "title": article.title,
                                "url": article.url,
                                "body_status": article.body_status,
                                "discovery_method": article.discovery_method,
                                "content_source": article.content_source,
                                "time_status": article.time_status,
                            }
                            for article in articles[: args.max_candidates]
                        ],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        crawler.close()


if __name__ == "__main__":
    main()
