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


def extract_published_at(html: str):
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
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                parsed = parse_datetime(item.get("datePublished") or item.get("dateModified"))
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
