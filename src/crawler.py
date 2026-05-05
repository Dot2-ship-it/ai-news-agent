from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .extractor import extract_article_text, extract_article_title, extract_published_at
from .models import Article, ArticleCandidate, SourceConfig, SourceRunResult
from .utils import clean_url, normalize_title, now_utc

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


class NewsCrawler:
    def __init__(self, timeout: float = 20.0) -> None:
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, headers=BROWSER_HEADERS)

    def close(self) -> None:
        self.client.close()

    def _headers_for_url(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        headers = dict(BROWSER_HEADERS)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        return headers

    def _get(self, url: str) -> httpx.Response:
        response = self.client.get(url, headers=self._headers_for_url(url))
        if response.status_code == 403:
            logger.warning("403 forbidden: %s", url)
        response.raise_for_status()
        return response

    @staticmethod
    def _network_error_message(url: str) -> str:
        return f"DNS 解析失败或网络不可达：{url}"

    def discover_candidates(self, source: SourceConfig) -> list[ArticleCandidate]:
        source_url = str(source.url)
        response = self._get(source_url)
        soup = BeautifulSoup(response.text, "lxml")

        raw_links = self._extract_jiqizhixin_links(response.text, source_url) if source.id == "jiqizhixin" else []
        raw_links.extend((a.get("href"), a.get_text(" ", strip=True) or None) for a in soup.find_all("a", href=True))

        candidates: list[ArticleCandidate] = []
        seen: set[str] = set()
        for href, title in raw_links:
            if not href:
                continue
            url = clean_url(href, source_url)
            if url in seen or not self._is_allowed(url, source):
                continue
            candidates.append(
                ArticleCandidate(
                    source_id=source.id,
                    source_name=source.name,
                    source_language=source.language,
                    source_strategy=source.strategy,
                    title=title,
                    url=url,
                )
            )
            seen.add(url)
            if len(candidates) >= source.max_links:
                break
        return candidates

    def _extract_jiqizhixin_links(self, html: str, base_url: str) -> list[tuple[str, str | None]]:
        links: list[tuple[str, str | None]] = []
        soup = BeautifulSoup(html, "lxml")

        selectors = [
            'a[href^="/articles/"]',
            'a[href*="jiqizhixin.com/articles/"]',
            ".article-item a[href]",
            ".news-item a[href]",
            ".u-block a[href]",
        ]
        for selector in selectors:
            for node in soup.select(selector):
                href = node.get("href")
                title = node.get_text(" ", strip=True) or node.get("title")
                if href:
                    links.append((href, title))

        # 机器之心首页常把文章路径藏在前端 JSON/script 中，普通 a 标签可能抓不到。
        for match in re.finditer(r'(?P<url>https?://www\.jiqizhixin\.com/articles/\d+|/articles/\d+)', html):
            links.append((match.group("url"), None))

        # 去重并把相对路径标准化，保留原始标题线索。
        deduped: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for href, title in links:
            url = clean_url(href, base_url)
            if url not in seen:
                deduped.append((url, title))
                seen.add(url)
        return deduped

    def fetch_article(self, candidate: ArticleCandidate) -> Article | None:
        response = self._get(candidate.url)
        html = response.text
        title = extract_article_title(html) or candidate.title
        content = extract_article_text(html, candidate.url)
        if not title or len(content) < 300:
            logger.info("Filtered weak article extraction: %s", candidate.url)
            return None
        return Article(
            source_id=candidate.source_id,
            source_name=candidate.source_name,
            source_language=candidate.source_language,
            source_strategy=candidate.source_strategy,
            title=title,
            url=clean_url(str(response.url)),
            published_at=extract_published_at(html),
            content=content,
        )

    def crawl_source(self, source: SourceConfig, lookback_hours: int) -> tuple[list[Article], SourceRunResult]:
        result = SourceRunResult(source_id=source.id, source_name=source.name)
        articles: list[Article] = []
        cutoff = now_utc() - timedelta(hours=lookback_hours)

        try:
            candidates = self.discover_candidates(source)
            result.candidates = len(candidates)
            logger.info("%s discovered %s candidate links", source.name, len(candidates))
        except httpx.HTTPStatusError as exc:
            result.failed = True
            result.error = f"HTTP {exc.response.status_code}: {exc.request.url}"
            logger.warning("%s skipped: %s", source.name, result.error)
            return articles, result
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
            result.failed = True
            result.error = self._network_error_message(str(source.url))
            logger.warning("%s skipped: %s (%s)", source.name, result.error, exc)
            return articles, result
        except Exception as exc:
            result.failed = True
            result.error = str(exc)
            logger.exception("Failed to discover links from %s", source.name)
            return articles, result

        seen_titles: set[str] = set()
        seen_urls: set[str] = set()
        for candidate in candidates:
            try:
                article = self.fetch_article(candidate)
                result.fetched += 1
            except httpx.HTTPStatusError as exc:
                reason = f"HTTP {exc.response.status_code}：{candidate.url}"
                logger.warning("%s filtered: %s", source.name, reason)
                result.filtered.append(reason)
                continue
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as exc:
                reason = self._network_error_message(candidate.url)
                logger.warning("%s filtered: %s (%s)", source.name, reason, exc)
                result.filtered.append(reason)
                continue
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", candidate.url, exc)
                result.filtered.append(f"抓取失败：{candidate.url}")
                continue

            if not article:
                result.filtered.append(f"正文提取不足：{candidate.url}")
                continue
            normalized_title = normalize_title(article.title)
            if article.url in seen_urls or normalized_title in seen_titles:
                result.filtered.append(f"列表内重复：{article.title}")
                continue
            if article.published_at and article.published_at < cutoff:
                result.filtered.append(f"超过时间窗口：{article.title}")
                continue
            seen_urls.add(article.url)
            seen_titles.add(normalized_title)
            articles.append(article)

        result.kept = len(articles)
        logger.info("%s kept %s articles, filtered %s", source.name, result.kept, len(result.filtered))
        return articles, result

    def _is_allowed(self, url: str, source: SourceConfig) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if source.allowed_domains and parsed.netloc not in source.allowed_domains:
            return False
        if source.include_patterns and not any(pattern in parsed.path for pattern in source.include_patterns):
            return False
        if source.exclude_patterns and any(pattern in url for pattern in source.exclude_patterns):
            return False
        return True
