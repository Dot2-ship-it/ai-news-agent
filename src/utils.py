from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import yaml
from dateutil import parser as date_parser

from .models import AgentConfig

TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "spm", "from"}


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def load_config(path: str | Path) -> AgentConfig:
    with Path(path).open("r", encoding="utf-8") as f:
        return AgentConfig.model_validate(yaml.safe_load(f))


def clean_url(url: str, base_url: str | None = None) -> str:
    absolute = urljoin(base_url, url) if base_url else url
    parsed = urlparse(absolute)
    kept = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith(TRACKING_PREFIXES) or lower_key in TRACKING_PARAMS:
            continue
        kept.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(kept, doseq=True), fragment=""))


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compact_text(text: str, max_chars: int = 12000) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:max_chars]
