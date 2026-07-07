from __future__ import annotations

import logging
import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from .models import ArticleCandidate, SourceConfig
from .utils import clean_url, parse_datetime

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
CACHE_PATH = Path(".cache/gdelt_cache.json")
CACHE_TTL_SECONDS = 12 * 60 * 60
_CALLED_DOMAIN_KEYS: set[str] = set()
_GDELT_CALL_USED = False
_GDELT_RATE_LIMITED = False

GDELT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "close",
}


@dataclass
class GdeltDiscoveryResult:
    candidates: list[ArticleCandidate]
    status: str = "skipped"
    error_message: str | None = None


def discover_gdelt_candidates(source: SourceConfig, max_records: int | None = None) -> GdeltDiscoveryResult:
    global _GDELT_CALL_USED, _GDELT_RATE_LIMITED
    domains = source.gdelt_domains or [domain for domain in source.allowed_domains if domain]
    terms = source.gdelt_query_terms or ["artificial intelligence", "AI"]
    if not domains:
        return GdeltDiscoveryResult([], "skipped", "missing gdelt domain")

    query_parts = [f'domain:{domain.removeprefix("www.")}' for domain in domains]
    domain_key = ",".join(sorted(domain.removeprefix("www.") for domain in domains))
    cached = _read_cache(domain_key, source)
    if cached is not None:
        return GdeltDiscoveryResult(cached, "success", None)
    if _GDELT_RATE_LIMITED:
        return GdeltDiscoveryResult([], "rate_limited", "GDELT rate_limited earlier in this run")
    if _GDELT_CALL_USED:
        return GdeltDiscoveryResult([], "skipped", "GDELT global call limit reached in this run")
    if domain_key in _CALLED_DOMAIN_KEYS:
        return GdeltDiscoveryResult([], "skipped", "domain already called in this run")
    _CALLED_DOMAIN_KEYS.add(domain_key)
    _GDELT_CALL_USED = True

    term_query = " OR ".join(f'"{term}"' if " " in term else term for term in terms[:12])
    domain_query = " OR ".join(query_parts)
    query = f"({term_query}) ({domain_query})"

    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "timespan": "1d",
        "maxrecords": str(max_records or min(source.max_links, 20)),
        "sort": "HybridRel",
    }
    url = f"{GDELT_DOC_API}?{'&'.join(f'{key}={quote_plus(value)}' for key, value in params.items())}"

    try:
        response = _request_once(url)
        data = response.json()
    except httpx.HTTPStatusError as exc:
        status = "rate_limited" if exc.response.status_code == 429 else "failed"
        message = f"HTTP {exc.response.status_code}: {exc.request.url}"
        if exc.response.status_code == 429:
            _GDELT_RATE_LIMITED = True
        logger.warning("GDELT fallback %s for %s: %s", status, source.id, message)
        return GdeltDiscoveryResult([], status, message)
    except Exception as exc:
        logger.warning("GDELT fallback failed for %s: %s", source.id, exc)
        return GdeltDiscoveryResult([], "failed", str(exc))

    candidates: list[ArticleCandidate] = []
    seen: set[str] = set()
    for article in data.get("articles", []):
        raw_url = str(article.get("url") or "").strip()
        if not raw_url:
            continue
        article_url = clean_url(raw_url, str(source.url))
        title = str(article.get("title") or "").strip()
        if not article_url or article_url in seen or not title:
            continue
        seen.add(article_url)
        candidates.append(
            ArticleCandidate(
                source_id=source.id,
                source_name=source.name,
                source_language=source.language,
                source_strategy=source.strategy,
                source_type=source.source_type,
                quality_tier=source.quality_tier,
                region=source.region,
                title=title,
                url=article_url,
                published_at=parse_datetime(str(article.get("seendate") or "")),
                body_status="body_unavailable",
                discovery_status="gdelt_fallback",
                content_source="gdelt",
                discovery_method="gdelt",
                is_partial=True,
                partial_reason="discovered_via_gdelt",
            )
        )
        if len(candidates) >= source.max_links:
            break
    _write_cache(domain_key, candidates)
    return GdeltDiscoveryResult(candidates, "success", None)


def _request_once(url: str) -> httpx.Response:
    response = httpx.get(url, headers=GDELT_HEADERS, timeout=20, follow_redirects=True)
    response.raise_for_status()
    return response


def _read_cache(domain_key: str, source: SourceConfig) -> list[ArticleCandidate] | None:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get(domain_key)
    if not entry or time.time() - float(entry.get("created_at", 0)) > CACHE_TTL_SECONDS:
        return None
    candidates = []
    for item in entry.get("candidates", []):
        try:
            candidates.append(ArticleCandidate.model_validate({**item, "source_id": source.id, "source_name": source.name}))
        except Exception:
            continue
    return candidates


def _write_cache(domain_key: str, candidates: list[ArticleCandidate]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        data[domain_key] = {
            "created_at": time.time(),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
        CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write GDELT cache: %s", exc)
