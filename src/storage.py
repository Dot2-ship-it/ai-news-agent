from __future__ import annotations

import json
from pathlib import Path

from .models import Article
from .utils import content_hash, normalize_title


class SeenStore:
    def __init__(self, path: str | Path = ".cache/seen_articles.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen_urls: set[str] = set()
        self._seen_titles: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        self._seen_urls = set(payload.get("urls", []))
        self._seen_titles = set(payload.get("titles", []))

    def has_seen_article(self, article: Article) -> bool:
        return article.url in self._seen_urls or normalize_title(article.title) in self._seen_titles

    def add_articles(self, articles: list[Article]) -> None:
        for article in articles:
            self._seen_urls.add(article.url)
            self._seen_titles.add(normalize_title(article.title))
        payload = {
            "urls": sorted(self._seen_urls),
            "titles": sorted(self._seen_titles),
            "fingerprint": content_hash("\n".join(sorted(self._seen_urls))),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
