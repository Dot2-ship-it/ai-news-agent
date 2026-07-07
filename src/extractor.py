from __future__ import annotations

import json
import logging
import re

import trafilatura
from bs4 import BeautifulSoup
from readability import Document

from .utils import compact_text, parse_datetime

logger = logging.getLogger(__name__)


def extract_article_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for selector in ['meta[property="og:title"]', 'meta[name="twitter:title"]']:
        node = soup.select_one(selector)
        if node and node.get("content"):
            return node["content"].strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return soup.title.get_text(" ", strip=True) if soup.title else None


def _normalize_datetime_text(value: str) -> str:
    return (
        value.replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .replace("时", ":")
        .replace("分", "")
    )


def _iter_json_ld_items(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_json_ld_items(item)
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _iter_json_ld_items(item)


def extract_published_at(html: str, url: str | None = None):
    soup = BeautifulSoup(html, "lxml")
    selectors = [
        'meta[property="article:published_time"]',
        'meta[name="pubdate"]',
        'meta[name="publishdate"]',
        'meta[name="date"]',
        "time[datetime]",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        value = node.get("content") or node.get("datetime") if node else None
        parsed = parse_datetime(value)
        if parsed:
            return parsed

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for item in _iter_json_ld_items(data):
            parsed = parse_datetime(item.get("datePublished") or item.get("dateModified"))
            if parsed:
                return parsed

    text = soup.get_text(" ", strip=True)
    patterns = [
        r"(?:发布时间|发布于|发表时间|时间)[:：\s]*([0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}(?:\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?)",
        r"(?:发布时间|发布于|发表时间|时间)[:：\s]*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日(?:\s*[0-9]{1,2}时[0-9]{1,2}分?)?)",
        r"([0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}\s+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)",
        r"([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parsed = parse_datetime(_normalize_datetime_text(match.group(1)))
        if parsed:
            return parsed

    if url:
        match = re.search(r"/([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})(?:/|[-_])", url)
        if match:
            parsed = parse_datetime("-".join(match.groups()))
            if parsed:
                return parsed
        match = re.search(r"/([0-9]{4})/([0-9]{1,2})/", url)
        if match:
            parsed = parse_datetime("-".join([match.group(1), match.group(2), "01"]))
            if parsed:
                return parsed
    return None


def extract_article_text(html: str, url: str) -> str:
    extracted = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    )
    if extracted:
        return compact_text(extracted)

    try:
        html = Document(html).summary(html_partial=True)
    except Exception as exc:
        logger.debug("readability failed for %s: %s", url, exc)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return compact_text(re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True)))
