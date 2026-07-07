from __future__ import annotations

import logging
import os
import re
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .extractor import extract_article_text, extract_article_title, extract_published_at
from .gdelt_discovery import discover_gdelt_candidates
from .investment_filter import is_preferred_article_path, is_url_noise, score_article
from .models import AgentConfig, Article, ArticleCandidate, SourceConfig, SourceRunResult
from .utils import clean_url, normalize_title, parse_datetime

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Connection": "close",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

FETCH_FALLBACK_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.LocalProtocolError,
    httpx.NetworkError,
    httpx.ProtocolError,
)

AI_KEYWORDS = (
    "ai",
    "人工智能",
    "大模型",
    "模型",
    "agent",
    "智能体",
    "openai",
    "deepseek",
    "claude",
    "gemini",
    "算力",
    "芯片",
    "gpu",
    "nvidia",
    "英伟达",
    "机器人",
    "自动驾驶",
    "ai 应用",
    "多模态",
    "推理模型",
)

QBITAI_NAV_TITLE_KEYWORDS = ("智库", "活动", "首页", "专题", "量子位", "meet")


@dataclass
class FetchResponse:
    url: str
    text: str
    status_code: int


class FetchError(Exception):
    def __init__(self, url: str, error_type: str, message: str) -> None:
        super().__init__(message)
        self.url = url
        self.error_type = error_type
        self.message = message


class FetchHTTPStatusError(FetchError):
    def __init__(self, url: str, status_code: int) -> None:
        super().__init__(url, "HTTPStatusError", f"HTTP {status_code}: {url}")
        self.status_code = status_code


class NewsCrawler:
    def __init__(self, timeout: float = 20.0, config: AgentConfig | None = None) -> None:
        try:
            import certifi

            verify: str | bool = certifi.where()
        except ImportError:
            verify = True
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, headers=BROWSER_HEADERS, verify=verify)
        self.config = config
        self.tracked_companies = config.tracked_companies if config else {}
        self.time_filter = config.time_filter if config else {}

    def close(self) -> None:
        self.client.close()

    def _headers_for_url(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        headers = dict(BROWSER_HEADERS)
        if parsed.netloc.endswith("sec.gov"):
            headers["User-Agent"] = os.getenv("SEC_USER_AGENT", "AI News Agent contact: no-reply@example.com")
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        return headers

    def _get(self, url: str) -> FetchResponse:
        return self.fetch_url(url)

    def fetch_url(self, url: str) -> FetchResponse:
        headers = self._headers_for_url(url)
        try:
            response = self.client.get(url, headers=headers)
            if response.status_code == 403:
                logger.warning("403 forbidden: %s", url)
            if response.status_code in {401, 403, 429}:
                logger.warning(
                    "httpx returned %s, trying curl_cffi fallback: url=%s",
                    response.status_code,
                    url,
                )
                return self._fetch_with_curl_cffi(url, headers, FetchHTTPStatusError(str(response.url), response.status_code))
            if response.status_code >= 400:
                raise FetchHTTPStatusError(str(response.url), response.status_code)
            return FetchResponse(url=str(response.url), text=response.text, status_code=response.status_code)
        except FetchHTTPStatusError:
            raise
        except FETCH_FALLBACK_EXCEPTIONS as primary_exc:
            logger.warning(
                "httpx failed, trying curl_cffi fallback: url=%s error_type=%s error_message=%s",
                url,
                type(primary_exc).__name__,
                primary_exc,
            )
            return self._fetch_with_curl_cffi(url, headers, primary_exc)

    def _fetch_with_curl_cffi(
        self,
        url: str,
        headers: dict[str, str],
        primary_exc: Exception,
    ) -> FetchResponse:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            raise FetchError(
                url=url,
                error_type="CurlCffiMissing",
                message=(
                    f"httpx {type(primary_exc).__name__}: {primary_exc}; "
                    "curl_cffi is not installed"
                ),
            ) from exc

        try:
            response = curl_requests.get(
                url,
                headers=headers,
                timeout=20,
                allow_redirects=True,
                impersonate="chrome120",
            )
            if response.status_code == 403:
                logger.warning("403 forbidden via curl_cffi: %s", url)
            if response.status_code >= 400:
                raise FetchHTTPStatusError(str(response.url), response.status_code)
            return FetchResponse(url=str(response.url), text=response.text, status_code=response.status_code)
        except FetchHTTPStatusError:
            raise
        except Exception as fallback_exc:
            error_type = type(fallback_exc).__name__
            if "no alternative certificate subject name" in str(fallback_exc).lower():
                error_type = "ssl_hostname_mismatch"
            raise FetchError(
                url=url,
                error_type=error_type,
                message=(
                    f"httpx {type(primary_exc).__name__}: {primary_exc}; "
                    f"curl_cffi {type(fallback_exc).__name__}: {fallback_exc}"
                ),
            ) from fallback_exc

    @staticmethod
    def _network_error_message(url: str) -> str:
        return f"抓取失败：{url}"

    def discover_candidates(self, source: SourceConfig, result: SourceRunResult | None = None) -> list[ArticleCandidate]:
        if source.source_type == "sec_filing" and source.sec_ciks:
            return self._discover_sec_filings(source)

        candidates: list[ArticleCandidate] = []
        seen: set[str] = set()
        urls = [str(source.url), *source.alternate_urls]
        last_error: Exception | None = None
        for source_url in urls:
            try:
                response = self._get(source_url)
            except Exception as exc:
                last_error = exc
                if result:
                    result.discovery_failed_count += 1
                continue

            soup = BeautifulSoup(response.text, "xml" if source.fetch_strategy.discovery_mode == "rss" else "lxml")
            rss_meta: dict[str, tuple[str | None, datetime | None]] = {}
            if source.fetch_strategy.discovery_mode == "rss":
                raw_links = []
                for item in soup.find_all("item"):
                    link = item.find("link")
                    title = item.find("title")
                    href = link.get_text(strip=True) if link else None
                    summary = item.find("description")
                    published = item.find("pubDate")
                    raw_links.append((href, title.get_text(" ", strip=True) if title else None))
                    if href:
                        rss_meta[clean_url(href, source_url)] = (
                            summary.get_text(" ", strip=True) if summary else None,
                            parse_datetime(published.get_text(" ", strip=True) if published else None),
                        )
            else:
                raw_links = self._extract_jiqizhixin_links(response.text, source_url) if source.id == "jiqizhixin" else []
                for candidate in self._extract_list_page_candidates(source, response.text, source_url):
                    if candidate.url not in seen and self._is_allowed(candidate.url, source, candidate.title):
                        candidates.append(candidate)
                        seen.add(candidate.url)
                        if len(candidates) >= source.max_links:
                            return candidates
                raw_links.extend((a.get("href"), a.get_text(" ", strip=True) or None) for a in soup.find_all("a", href=True))

            for href, title in raw_links:
                if not href:
                    continue
                url = clean_url(href, source_url)
                if url in seen or not self._is_allowed(url, source, title):
                    if result and url not in seen:
                        result.filtered_by_url_count += 1
                    continue
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
                        url=url,
                        summary=rss_meta.get(url, (None, None))[0],
                        published_at=rss_meta.get(url, (None, None))[1],
                        content_source="official_ir_rss" if source.fetch_strategy.discovery_mode == "rss" else "list_page",
                        discovery_method="ir_rss" if source.fetch_strategy.discovery_mode == "rss" else "list_page",
                    )
                )
                seen.add(url)
                if len(candidates) >= source.max_links:
                    return candidates
        if not candidates:
            candidates = self._discover_lightweight_fallbacks(source, result)
        if not candidates and last_error:
            raise last_error
        return candidates

    def _discover_sec_filings(self, source: SourceConfig) -> list[ArticleCandidate]:
        candidates: list[ArticleCandidate] = []
        forms = set(source.sec_forms or ["8-K", "10-Q", "10-K"])
        for ticker, cik in source.sec_ciks.items():
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            time.sleep(0.5)
            response = self._get(url)
            data = json.loads(response.text)
            recent = data.get("filings", {}).get("recent", {})
            for idx, form in enumerate(recent.get("form", [])):
                if form not in forms:
                    continue
                accession = recent.get("accessionNumber", [""])[idx]
                document = recent.get("primaryDocument", [""])[idx]
                filing_date = recent.get("filingDate", [""])[idx]
                accepted_at = recent.get("acceptanceDateTime", [""])[idx]
                if not accession or not document:
                    continue
                accession_path = accession.replace("-", "")
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{document}"
                candidates.append(
                    ArticleCandidate(
                        source_id=source.id,
                        source_name=source.name,
                        source_language=source.language,
                        source_strategy=source.strategy,
                        source_type=source.source_type,
                        quality_tier=source.quality_tier,
                        region=source.region,
                        title=f"{ticker} {form} filed {filing_date}",
                        url=filing_url,
                        published_at=parse_datetime(accepted_at or filing_date),
                        content_source="sec_api",
                        discovery_method="sec_api",
                    )
                )
                if len(candidates) >= source.max_links:
                    return candidates
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

    def _discover_lightweight_fallbacks(
        self,
        source: SourceConfig,
        result: SourceRunResult | None = None,
    ) -> list[ArticleCandidate]:
        if source.id == "reuters_ai":
            endpoints = [
                ("rss", "https://www.reuters.com/arc/outboundfeeds/rss/category/technology/?outputType=xml"),
                ("sitemap", "https://www.reuters.com/sitemap.xml"),
                ("sitemap", "https://www.reuters.com/sitemap-news.xml"),
            ]
        elif source.id == "data_center_dynamics_ai":
            endpoints = [
                ("search_index", "https://www.datacenterdynamics.com/en/topics-and-tech/software/ai-analytics/"),
                ("search_index", "https://www.datacenterdynamics.com/en/topics-and-tech/cloud-hyperscale/"),
                ("rss", "https://www.datacenterdynamics.com/en/rss.xml"),
                ("rss", "https://www.datacenterdynamics.com/en/news/rss/"),
                ("sitemap", "https://www.datacenterdynamics.com/sitemap.xml"),
            ]
        else:
            return []

        candidates: list[ArticleCandidate] = []
        seen: set[str] = set()
        for method, url in endpoints:
            try:
                response = self._get(url)
            except Exception:
                if result:
                    result.discovery_failed_count += 1
                continue
            if method == "rss":
                new_candidates = self._extract_rss_candidates(source, response.text, url)
            elif method == "sitemap":
                new_candidates = self._extract_sitemap_candidates(source, response.text, url)
            else:
                new_candidates = self._extract_list_page_candidates(source, response.text, url, discovery_method="search_index")
            for candidate in new_candidates:
                if candidate.url in seen or not self._is_allowed(candidate.url, source, candidate.title):
                    continue
                candidates.append(candidate)
                seen.add(candidate.url)
                if len(candidates) >= source.max_links:
                    return candidates
        return candidates

    def _extract_rss_candidates(self, source: SourceConfig, xml: str, base_url: str) -> list[ArticleCandidate]:
        soup = BeautifulSoup(xml, "xml")
        candidates: list[ArticleCandidate] = []
        for item in soup.find_all("item"):
            link = item.find("link")
            title = item.find("title")
            summary = item.find("description")
            published = item.find("pubDate") or item.find("published") or item.find("updated")
            if not link or not title:
                continue
            candidate = self._candidate_from_list_fields(
                source,
                title.get_text(" ", strip=True),
                clean_url(link.get_text(strip=True), base_url),
                summary.get_text(" ", strip=True) if summary else None,
                parse_datetime(published.get_text(" ", strip=True) if published else None),
                "rss",
            )
            if candidate:
                candidates.append(candidate)
                if len(candidates) >= source.max_links:
                    break
        return candidates

    def _extract_sitemap_candidates(self, source: SourceConfig, xml: str, base_url: str) -> list[ArticleCandidate]:
        soup = BeautifulSoup(xml, "xml")
        locs = [loc.get_text(strip=True) for loc in soup.find_all("loc")]
        if any(loc.endswith(".xml") for loc in locs):
            candidates: list[ArticleCandidate] = []
            for loc in locs:
                if not self._sitemap_index_url_relevant(source, loc):
                    continue
                try:
                    response = self._get(loc)
                except Exception:
                    continue
                candidates.extend(self._extract_sitemap_candidates(source, response.text, loc))
                if len(candidates) >= source.max_links:
                    return candidates[: source.max_links]
            return candidates

        candidates = []
        for url_node in soup.find_all("url"):
            loc = url_node.find("loc")
            if not loc:
                continue
            url = clean_url(loc.get_text(strip=True), base_url)
            if not self._is_allowed(url, source):
                continue
            lastmod = url_node.find("lastmod")
            title = self._title_from_url(url)
            candidate = self._candidate_from_list_fields(
                source,
                title,
                url,
                None,
                parse_datetime(lastmod.get_text(strip=True) if lastmod else None),
                "sitemap",
            )
            if candidate:
                candidates.append(candidate)
                if len(candidates) >= source.max_links:
                    break
        return candidates

    @staticmethod
    def _sitemap_index_url_relevant(source: SourceConfig, url: str) -> bool:
        lowered = url.lower()
        if source.id == "reuters_ai":
            return any(part in lowered for part in ("technology", "artificial", "news"))
        if source.id == "data_center_dynamics_ai":
            return any(part in lowered for part in ("news", "post", "sitemap"))
        return False

    @staticmethod
    def _title_from_url(url: str) -> str:
        slug = urlparse(url).path.rstrip("/").split("/")[-1]
        slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", slug)
        return re.sub(r"[-_]+", " ", slug).strip().title() or url

    def _extract_list_page_candidates(
        self,
        source: SourceConfig,
        html: str,
        base_url: str,
        discovery_method: str = "list_page",
    ) -> list[ArticleCandidate]:
        if source.id not in {"reuters_ai", "data_center_dynamics_ai"}:
            return []

        candidates: list[ArticleCandidate] = []
        seen: set[str] = set()
        soup = BeautifulSoup(html, "lxml")

        containers = soup.select(
            "article, li, div[class*='card'], div[class*='story'], div[class*='article'], "
            "div[class*='media'], div[class*='promo'], div[class*='teaser'], div[class*='item']"
        )
        for node in containers:
            anchor = node.find("a", href=True)
            if not anchor:
                continue
            title_node = node.find(["h1", "h2", "h3", "h4"]) or anchor
            title = title_node.get_text(" ", strip=True)
            url = clean_url(anchor.get("href", ""), base_url)
            if not title or url in seen:
                continue
            summary = self._node_summary(node, title)
            published_at = self._node_date(node)
            candidate = self._candidate_from_list_fields(source, title, url, summary, published_at, discovery_method)
            if candidate:
                candidates.append(candidate)
                seen.add(url)
                if len(candidates) >= source.max_links:
                    return candidates

        for candidate in self._extract_script_candidates(source, html, base_url, discovery_method):
            if candidate.url not in seen:
                candidates.append(candidate)
                seen.add(candidate.url)
                if len(candidates) >= source.max_links:
                    return candidates
        return candidates

    def _extract_script_candidates(
        self,
        source: SourceConfig,
        html: str,
        base_url: str,
        discovery_method: str = "list_page",
    ) -> list[ArticleCandidate]:
        candidates: list[ArticleCandidate] = []
        seen: set[str] = set()
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script"):
            raw = script.string or script.get_text("", strip=False)
            if not raw or ("headline" not in raw and "title" not in raw and "description" not in raw):
                continue
            for obj in self._json_objects_from_script(raw):
                for item in self._walk_json_objects(obj):
                    title = self._first_string(item, ("headline", "title", "name", "kicker"))
                    url = self._first_string(item, ("url", "canonical_url", "canonicalUrl", "href", "link", "path"))
                    summary = self._first_string(item, ("description", "summary", "excerpt", "teaser", "dek"))
                    date_text = self._first_string(
                        item,
                        ("datePublished", "dateModified", "published_time", "publishedAt", "display_time", "date", "updated_time"),
                    )
                    if not title or not url:
                        continue
                    article_url = clean_url(url, base_url)
                    if article_url in seen:
                        continue
                    candidate = self._candidate_from_list_fields(
                        source,
                        title,
                        article_url,
                        summary,
                        self._parse_list_date(date_text),
                        discovery_method,
                    )
                    if candidate:
                        candidates.append(candidate)
                        seen.add(article_url)
                        if len(candidates) >= source.max_links:
                            return candidates
        return candidates

    @staticmethod
    def _json_objects_from_script(raw: str) -> list[object]:
        objects: list[object] = []
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                objects.append(json.loads(stripped))
                return objects
            except json.JSONDecodeError:
                pass
        for match in re.finditer(r"(\{[^<]{80,}\})", raw):
            try:
                objects.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                continue
        return objects

    def _walk_json_objects(self, value: object):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk_json_objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_json_objects(child)

    @staticmethod
    def _first_string(item: dict[str, object], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = value.get("url") or value.get("text")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
        return None

    def _candidate_from_list_fields(
        self,
        source: SourceConfig,
        title: str,
        url: str,
        summary: str | None,
        published_at: datetime | None,
        discovery_method: str = "list_page",
    ) -> ArticleCandidate | None:
        if not self._is_allowed(url, source, title):
            return None
        return ArticleCandidate(
            source_id=source.id,
            source_name=source.name,
            source_language=source.language,
            source_strategy=source.strategy,
            source_type=source.source_type,
            quality_tier=source.quality_tier,
            region=source.region,
            title=title,
            url=url,
            summary=summary,
            published_at=published_at,
            content_source="list_page",
            discovery_method=discovery_method,
        )

    def _node_summary(self, node, title: str) -> str | None:
        parts: list[str] = []
        for summary_node in node.find_all(["p", "span"], limit=6):
            text = summary_node.get_text(" ", strip=True)
            if text and text != title and len(text) > 25:
                parts.append(text)
        return " ".join(parts[:2]) or None

    def _node_date(self, node) -> datetime | None:
        time_node = node.find("time")
        if time_node:
            parsed = self._parse_list_date(time_node.get("datetime") or time_node.get_text(" ", strip=True))
            if parsed:
                return parsed
        text = node.get_text(" ", strip=True)
        return self._parse_list_date(text)

    @staticmethod
    def _parse_list_date(text: str | None) -> datetime | None:
        if not text:
            return None
        parsed = parse_datetime(text)
        if parsed:
            return parsed
        now = datetime.now(timezone.utc)
        lowered = text.lower()
        if "yesterday" in lowered:
            return now - timedelta(days=1)
        match = re.search(r"(\d+)\s+(minute|minutes|hour|hours|day|days)\s+ago", lowered)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if unit.startswith("minute"):
                return now - timedelta(minutes=amount)
            if unit.startswith("hour"):
                return now - timedelta(hours=amount)
            return now - timedelta(days=amount)
        month_match = re.search(
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
            r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}",
            text,
            flags=re.IGNORECASE,
        )
        if month_match:
            return parse_datetime(month_match.group(0))
        return None

    def _candidate_response(self, candidate: ArticleCandidate) -> FetchResponse:
        try:
            return self._get(candidate.url)
        except FetchError:
            mobile_url = self._cls_mobile_url(candidate.url)
            if candidate.source_id == "cls_ai_global" and mobile_url and mobile_url != candidate.url:
                return self._get(mobile_url)
            raise
        except FetchHTTPStatusError:
            mobile_url = self._cls_mobile_url(candidate.url)
            if candidate.source_id == "cls_ai_global" and mobile_url and mobile_url != candidate.url:
                return self._get(mobile_url)
            raise

    @staticmethod
    def _cls_mobile_url(url: str) -> str | None:
        match = re.search(r"/detail/(\d+)", url)
        if not match:
            return None
        return f"https://m.cls.cn/detail/{match.group(1)}"

    @staticmethod
    def _partial_article_from_candidate(candidate: ArticleCandidate, reason: str, content_source: str | None = None) -> Article:
        content = candidate.summary or candidate.title or "正文不可用，仅保留候选标题。"
        return Article(
            source_id=candidate.source_id,
            source_name=candidate.source_name,
            source_language=candidate.source_language,
            source_strategy=candidate.source_strategy,
            source_type=candidate.source_type,
            quality_tier=candidate.quality_tier,
            region=candidate.region,
            title=candidate.title or candidate.source_name,
            url=candidate.url,
            published_at=candidate.published_at,
            time_status="known" if candidate.published_at else "time_unknown",
            body_status="body_unavailable",
            discovery_status=candidate.discovery_status,
            content_source=content_source or candidate.content_source,
            discovery_method=candidate.discovery_method,
            is_partial=True,
            partial_reason=reason,
            content=content,
        )

    def fetch_article(self, candidate: ArticleCandidate) -> Article | None:
        response = self._candidate_response(candidate)
        html = response.text
        title = candidate.title if candidate.source_type == "sec_filing" else extract_article_title(html) or candidate.title
        content = extract_article_text(html, candidate.url)
        if not title or len(content) < 300:
            if title and candidate.source_type in {"market_news", "official_ir", "semiconductor_research"}:
                content = content or title
            else:
                logger.info("Filtered weak article extraction: %s", candidate.url)
                return None
        published_at = extract_published_at(html, str(response.url)) or candidate.published_at
        if not published_at and candidate.source_type == "sec_filing" and candidate.title:
            match = re.search(r"filed\s+([0-9]{4}-[0-9]{2}-[0-9]{2})", candidate.title)
            if match:
                published_at = parse_datetime(match.group(1))
        return Article(
            source_id=candidate.source_id,
            source_name=candidate.source_name,
            source_language=candidate.source_language,
            source_strategy=candidate.source_strategy,
            source_type=candidate.source_type,
            quality_tier=candidate.quality_tier,
            region=candidate.region,
            title=title,
            url=clean_url(str(response.url)),
            published_at=published_at,
            time_status="known" if published_at else "time_unknown",
            body_status="body_available",
            discovery_status=candidate.discovery_status,
            content_source=candidate.content_source,
            discovery_method=candidate.discovery_method,
            is_partial=False,
            partial_reason=None,
            content=content,
        )

    def crawl_source(
        self,
        source: SourceConfig,
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[list[Article], SourceRunResult]:
        result = SourceRunResult(source_id=source.id, source_name=source.name, source_type=source.source_type)
        articles: list[Article] = []
        source_start_time = self._source_start_time(source, start_time, end_time)

        try:
            candidates = self.discover_candidates(source, result)
            result.candidates = len(candidates)
            logger.info("%s discovered %s candidate links", source.name, len(candidates))
        except FetchHTTPStatusError as exc:
            result.error_type = exc.error_type
            result.error = exc.message
            candidates = self._fallback_candidates(source, result)
            if not candidates:
                result.failed = source.source_type != "premium_business_news"
                result.failed_count = 0 if source.source_type == "premium_business_news" else 1
                result.discovery_failed_count += 1
                result.premium_limited_count += 1 if source.source_type == "premium_business_news" else 0
                result.status = "premium_limited" if source.source_type == "premium_business_news" else "fetch_failed"
                self._set_discovery_error_message(result)
                logger.warning("%s skipped: %s", source.name, result.error)
                return articles, result
            result.candidates = len(candidates)
        except FetchError as exc:
            result.error_type = exc.error_type
            result.error = exc.message
            logger.warning(
                "fetch diagnostics source=%s url=%s error_type=%s error_message=%s",
                source.name,
                exc.url,
                exc.error_type,
                exc.message,
            )
            candidates = self._fallback_candidates(source, result)
            if not candidates:
                result.failed = source.source_type != "premium_business_news"
                result.failed_count = 0 if source.source_type == "premium_business_news" else 1
                result.discovery_failed_count += 1
                result.premium_limited_count += 1 if source.source_type == "premium_business_news" else 0
                result.status = "premium_limited" if source.source_type == "premium_business_news" else "fetch_failed"
                self._set_discovery_error_message(result)
                return articles, result
            result.candidates = len(candidates)
        except Exception as exc:
            result.error_type = type(exc).__name__
            result.error = str(exc)
            logger.exception("Failed to discover links from %s", source.name)
            candidates = self._fallback_candidates(source, result)
            if not candidates:
                result.failed = source.source_type != "premium_business_news"
                result.failed_count = 0 if source.source_type == "premium_business_news" else 1
                result.discovery_failed_count += 1
                result.premium_limited_count += 1 if source.source_type == "premium_business_news" else 0
                result.status = "premium_limited" if source.source_type == "premium_business_news" else "fetch_failed"
                self._set_discovery_error_message(result)
                return articles, result
            result.candidates = len(candidates)

        result.discovery_methods = sorted({candidate.discovery_method for candidate in candidates if candidate.discovery_method})

        seen_titles: set[str] = set()
        seen_urls: set[str] = set()
        for candidate in candidates:
            if source.fetch_strategy.body_mode == "disabled":
                article = self._partial_article_from_candidate(candidate, "premium_limited", "list_page")
                result.partial_count += 1
                result.premium_limited_count += 1
                result.list_page_only_count += 1
            else:
                try:
                    article = self.fetch_article(candidate)
                    result.fetched += 1
                except FetchHTTPStatusError as exc:
                    reason = f"HTTP {exc.status_code}：{candidate.url}"
                    logger.warning("%s filtered: %s", source.name, reason)
                    if self._allows_partial(source):
                        article = self._partial_article_from_candidate(candidate, f"body_http_{exc.status_code}")
                        result.partial_count += 1
                        result.body_failed_count += 1
                        result.list_page_only_count += 1
                    else:
                        result.failed_count += 1
                        result.filtered.append(reason)
                        continue
                except FetchError as exc:
                    reason = f"{self._network_error_message(candidate.url)}（{exc.error_type}: {exc.message}）"
                    logger.warning(
                        "fetch diagnostics source=%s url=%s error_type=%s error_message=%s",
                        source.name,
                        candidate.url,
                        exc.error_type,
                        exc.message,
                    )
                    if self._allows_partial(source):
                        article = self._partial_article_from_candidate(candidate, "body_fetch_failed")
                        result.partial_count += 1
                        result.body_failed_count += 1
                        result.list_page_only_count += 1
                    else:
                        result.failed_count += 1
                        result.filtered.append(reason)
                        continue
                except Exception as exc:
                    logger.warning("Failed to fetch %s: %s", candidate.url, exc)
                    if self._allows_partial(source):
                        article = self._partial_article_from_candidate(candidate, "body_fetch_failed")
                        result.partial_count += 1
                        result.body_failed_count += 1
                        result.list_page_only_count += 1
                    else:
                        result.failed_count += 1
                        result.filtered.append(f"抓取失败：{candidate.url}")
                        continue

            if not article:
                if self._allows_partial(source):
                    article = self._partial_article_from_candidate(candidate, "body_too_short")
                    result.partial_count += 1
                    result.body_failed_count += 1
                    result.list_page_only_count += 1
                else:
                    result.filtered.append(f"正文提取不足：{candidate.url}")
                    continue
            normalized_title = normalize_title(article.title)
            if article.url in seen_urls or normalized_title in seen_titles:
                result.filtered.append(f"列表内重复：{article.title}")
                continue

            decision = None
            if source.strict_ai_relevance or source.requires_ai_filter or source.requires_investment_signal_filter:
                decision = score_article(article, source, self.tracked_companies, source_start_time, end_time)
                if not decision.keep:
                    result.filtered_by_relevance_count += 1
                    result.filtered.append(f"{decision.reason}：{article.title}")
                    continue

            if article.published_at:
                published_at = article.published_at.astimezone(source_start_time.tzinfo)
                if published_at < source_start_time or published_at > end_time:
                    article.time_status = "filtered_by_time_window"
                    result.filtered_by_time_count += 1
                    result.filtered.append(f"超过时间窗口：{article.title}")
                    continue
                article.time_status = "published_within_window"
            else:
                logger.info("%s published_at missing: %s", source.name, article.url)
                article.time_status = "time_unknown"
                if source.requires_time_filter and not source.allow_unknown_time:
                    result.filtered_by_time_count += 1
                    result.filtered.append(f"发布时间缺失：{article.title}")
                    continue

            if decision:
                article.investment_score = decision.score
                article.matched_companies = decision.matched_companies
                article.matched_signals = decision.matched_signals
                article.topic = decision.topic
            seen_urls.add(article.url)
            seen_titles.add(normalized_title)
            articles.append(article)

        result.kept = len(articles)
        result.status = self._source_status(result)
        logger.info(
            "%s diagnostics: discovered=%s fetched=%s kept=%s filtered_by_time=%s "
            "filtered_by_relevance=%s filtered_by_url=%s partial=%s body_failed=%s "
            "discovery_failed=%s gdelt_fallback=%s gdelt_status=%s premium_limited=%s failed=%s status=%s error=%s",
            source.name,
            result.candidates,
            result.fetched,
            result.kept,
            result.filtered_by_time_count,
            result.filtered_by_relevance_count,
            result.filtered_by_url_count,
            result.partial_count,
            result.body_failed_count,
            result.discovery_failed_count,
            result.gdelt_fallback_count,
            result.gdelt_status,
            result.premium_limited_count,
            result.failed_count,
            result.status,
            result.error,
        )
        return articles, result

    def _fallback_candidates(self, source: SourceConfig, result: SourceRunResult) -> list[ArticleCandidate]:
        candidates: list[ArticleCandidate] = []
        if "gdelt" in source.fetch_strategy.fallback_discovery:
            gdelt_result = discover_gdelt_candidates(source)
            candidates = gdelt_result.candidates
            result.gdelt_fallback_count += len(candidates)
            result.gdelt_status = gdelt_result.status
            result.gdelt_error_message = gdelt_result.error_message
            if candidates:
                result.error = None
                result.error_type = None
        return candidates

    @staticmethod
    def _set_discovery_error_message(result: SourceRunResult) -> None:
        primary = result.error or "unknown primary discovery error"
        result.error = f"primary_discovery_failed: {primary}"
        if result.gdelt_status == "rate_limited":
            result.error = f"{result.error}; gdelt_rate_limited: {result.gdelt_error_message or 'rate limited'}"
        elif result.gdelt_status == "failed" and result.gdelt_error_message:
            result.error = f"{result.error}; gdelt_failed: {result.gdelt_error_message}"

    @staticmethod
    def _allows_partial(source: SourceConfig) -> bool:
        return source.fetch_strategy.allow_partial_article or source.fetch_strategy.body_mode in {"optional", "disabled"}

    def _is_allowed(self, url: str, source: SourceConfig, title: str | None = None) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if source.allowed_domains and parsed.netloc not in source.allowed_domains:
            return False
        if source.include_patterns and not any(pattern in parsed.path for pattern in source.include_patterns):
            return False
        if source.exclude_patterns and any(pattern in url for pattern in source.exclude_patterns):
            return False
        if source.id == "reuters_ai" and any(
            part in parsed.path for part in ("/pictures/", "/video/", "/graphics/", "/live-", "/live/")
        ):
            return False
        if is_url_noise(parsed.path, title):
            return False
        if source.source_type in {"chinese_industry", "chinese_flash", "flash_news"} and not is_preferred_article_path(parsed.path):
            return False
        if source.id == "qbitai" and not self._is_qbitai_article_url(parsed.path, title):
            return False
        return True

    def _source_start_time(self, source: SourceConfig, start_time: datetime, end_time: datetime) -> datetime:
        if source.source_type == "official_ir":
            hours = int(self.time_filter.get("official_ir_window_hours", 72))
            return end_time - timedelta(hours=hours)
        if source.source_type == "sec_filing":
            hours = int(self.time_filter.get("sec_filing_window_hours", 48))
            return end_time - timedelta(hours=hours)
        return start_time

    @staticmethod
    def _source_status(result: SourceRunResult) -> str:
        if result.candidates > 0 and result.kept > 0:
            if result.body_failed_count > 0 or result.partial_count > 0:
                return "partial_success"
            return "success"
        if result.premium_limited_count:
            return "premium_limited"
        if result.candidates > 0 and result.kept == 0:
            if result.filtered_by_time_count and result.filtered_by_relevance_count == 0:
                return "filtered_by_time_window"
            return "no_investment_relevant_articles"
        return "fetch_failed"

    @staticmethod
    def _is_qbitai_article_url(path: str, title: str | None = None) -> bool:
        if not re.fullmatch(r"/\d{4}/\d{2}/[^/]+\.html", path):
            return False
        if title and any(keyword in title for keyword in QBITAI_NAV_TITLE_KEYWORDS):
            return False
        return True

    @staticmethod
    def _is_ai_relevant(article: Article) -> bool:
        haystack = f"{article.title}\n{article.content[:1200]}".lower()
        return any(keyword.lower() in haystack for keyword in AI_KEYWORDS)
