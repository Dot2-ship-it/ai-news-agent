from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import yaml

from .models import DailyDigest, NewsItem, WatchItem
from .utils import normalize_title

THEMES_PATH = Path("config/themes.yaml")
THEME_HISTORY_PATH = Path("theme_history.json")
SIGNAL_STATUS_LABELS = {
    "first_seen": "本轮首次记录",
    "new": "本轮首次记录",
    "continuing": "延续",
    "escalating": "升温",
    "weakening": "降温",
    "reversing": "待确认",
    "noise": "待确认",
}


@dataclass
class FollowUpVariable:
    name: str
    direction_to_watch: str
    why: str


@dataclass
class ThemeDefinition:
    id: str
    name: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class DigestEvent:
    event_id: str
    title: str
    source: str
    published_at: str | None
    canonical_url: str
    direct_companies: list[str]
    peer_companies: list[str]
    watch_companies: list[str]
    industry_layer: str
    signal_type: str
    investment_implication: str
    fact: str
    transmission_chain: str
    follow_up_variables: list[FollowUpVariable]
    confidence_level: str
    evidence_level: str
    signal_status: str
    theme_ids: list[str]
    relevance_score: int
    importance: str
    is_watch: bool = False
    is_partial: bool = False


@dataclass
class ThemeChange:
    theme_id: str
    theme_name: str
    signal_status: str
    evidence_count: int
    high_weight_count: int
    today_event_titles: list[str] = field(default_factory=list)
    new_event_titles: list[str] = field(default_factory=list)
    history_available: bool = False


@dataclass
class EventBundle:
    events: list[DigestEvent]
    core_events: list[DigestEvent]
    watch_events: list[DigestEvent]
    theme_changes: list[ThemeChange]
    follow_up_events: list[DigestEvent]


def build_event_bundle(digest: DailyDigest, max_core_events: int = 3) -> EventBundle:
    themes = load_themes()
    events_by_id: dict[str, DigestEvent] = {}
    for item in digest.items:
        event = event_from_item(item, themes)
        existing = events_by_id.get(event.event_id)
        if not existing or _event_rank(event) > _event_rank(existing):
            events_by_id[event.event_id] = event

    events = sorted(events_by_id.values(), key=_event_rank, reverse=True)
    core_events = [event for event in events if is_core_event(event)][:max_core_events]
    selected_ids = {event.event_id for event in core_events}
    watch_events = []
    for watch_item in digest.watchlist:
        event = event_from_watch_item(watch_item, themes)
        if event.event_id not in selected_ids and is_readable_watch_event(event):
            watch_events.append(event)
    watch_events = sorted(watch_events, key=_event_rank, reverse=True)[:8]
    return EventBundle(
        events=events,
        core_events=core_events,
        watch_events=watch_events,
        theme_changes=build_theme_changes([*core_events, *watch_events], themes),
        follow_up_events=build_follow_up_events([*core_events, *watch_events]),
    )


def load_themes(path: Path = THEMES_PATH) -> list[ThemeDefinition]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        ThemeDefinition(
            id=str(item.get("id", "")),
            name=str(item.get("name", "")),
            keywords=[str(keyword) for keyword in item.get("keywords", [])],
        )
        for item in data.get("themes", [])
        if item.get("id") and item.get("name")
    ]


def event_from_item(item: NewsItem, themes: list[ThemeDefinition]) -> DigestEvent:
    text = "\n".join([item.title, item.core_fact, item.important_meaning, " ".join(item.key_points)])
    direct = item.direct_companies or item.company_layer
    evidence = infer_evidence_level(item.source)
    confidence = infer_confidence_level(evidence, item.is_partial, item.time_status, item.discovery_method)
    status = infer_signal_status(text)
    event = DigestEvent(
        event_id=build_event_id(item.url, item.title, direct, item.industry_layer or item.topic, item.published_at),
        title=item.title,
        source=item.source,
        published_at=item.published_at,
        canonical_url=canonicalize_url(item.url),
        direct_companies=direct[:8],
        peer_companies=item.inferred_companies[:8],
        watch_companies=item.watch_companies[:6],
        industry_layer=item.industry_layer or item.topic,
        signal_type=item.signal_type or "产业链信号",
        investment_implication=item.important_meaning,
        fact=item.core_fact,
        transmission_chain=item.transmission_chain or "该事件会传导至相关公司的收入、成本和估值假设。",
        follow_up_variables=structure_follow_up_variables(item.watch_variables, text),
        confidence_level=confidence,
        evidence_level=evidence,
        signal_status=status,
        theme_ids=map_theme_ids(text, item.industry_layer or "", item.signal_type or "", themes),
        relevance_score=item.investment_score,
        importance=item.importance,
        is_partial=item.is_partial,
    )
    return event


def event_from_watch_item(item: WatchItem, themes: list[ThemeDefinition]) -> DigestEvent:
    text = f"{item.title}\n{item.signal_type}\n{item.industry_layer}"
    evidence = infer_evidence_level(item.source)
    status = "noise" if "time_unknown" in item.status and item.discovery_method == "list_page" else infer_signal_status(text)
    return DigestEvent(
        event_id=build_event_id(item.url, item.title, item.direct_companies or item.company_layer, item.industry_layer, None),
        title=item.title,
        source=item.source,
        published_at=None,
        canonical_url=canonicalize_url(item.url),
        direct_companies=(item.direct_companies or item.company_layer)[:8],
        peer_companies=item.inferred_companies[:8],
        watch_companies=item.watch_companies[:6],
        industry_layer=item.industry_layer,
        signal_type=item.signal_type,
        investment_implication="证据仍不足，先保留为观察线索。",
        fact=item.title,
        transmission_chain="该线索需要更多正文、时间和来源交叉验证后再进入主日报。",
        follow_up_variables=structure_follow_up_variables(item.watch_variables, text),
        confidence_level="低" if "time_unknown" in item.status else "中",
        evidence_level=evidence,
        signal_status=status,
        theme_ids=map_theme_ids(text, item.industry_layer, item.signal_type, themes),
        relevance_score=item.score,
        importance="低",
        is_watch=True,
        is_partial=True,
    )


def build_event_id(
    canonical_url: str,
    title: str,
    companies: list[str],
    topic: str | None,
    published_at: str | None,
) -> str:
    url = canonicalize_url(canonical_url)
    if url:
        return "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    normalized = normalize_title(title)
    if normalized:
        return "title:" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    fallback = "|".join([",".join(companies), topic or "", (published_at or "")[:10]])
    return "event:" + hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:12]


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


def infer_evidence_level(source: str) -> str:
    lowered = source.lower()
    if any(key in lowered for key in ("sec", "edgar", "investor", "ir", "official")):
        return "A"
    if any(key in lowered for key in ("reuters", "bloomberg", "ft", "wsj", "information", "semianalysis")):
        return "B"
    if any(key in lowered for key in ("36氪", "晚点", "latepost", "qbitai", "机器之心", "data center dynamics", "dcd")):
        return "C"
    if any(key in lowered for key in ("rumor", "传闻", "unconfirmed")):
        return "E"
    return "D"


def infer_confidence_level(
    evidence_level: str,
    is_partial: bool,
    time_status: str | None,
    discovery_method: str | None,
) -> str:
    if evidence_level in {"A", "B"} and not is_partial and time_status not in {"time_unknown", "unknown"}:
        return "高"
    if evidence_level in {"D", "E"} or (time_status in {"time_unknown", "unknown"} and discovery_method == "list_page"):
        return "低"
    return "中"


def infer_signal_status(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("反转", "转向", "reverse", "reversing")):
        return "new"
    if any(word in lowered for word in ("扩大", "升级", "加码", "accelerate", "expand", "escalat")):
        return "escalating"
    if any(word in lowered for word in ("放缓", "下修", "回落", "减弱", "slow", "weaken", "decline")):
        return "weakening"
    if any(word in lowered for word in ("继续", "延续", "仍", "continued", "still")):
        return "continuing"
    return "new"


def map_theme_ids(text: str, industry_layer: str, signal_type: str, themes: list[ThemeDefinition]) -> list[str]:
    haystack = f"{text}\n{industry_layer}\n{signal_type}".lower()
    matched = [
        theme.id
        for theme in themes
        if any(str(keyword).lower() in haystack for keyword in theme.keywords)
    ]
    return matched[:3] or ["ai_app_api_revenue"]


def structure_follow_up_variables(names: list[str], text: str) -> list[FollowUpVariable]:
    variables = []
    for name in names[:4]:
        variables.append(
            FollowUpVariable(
                name=name,
                direction_to_watch=infer_direction(name, text),
                why=infer_variable_reason(name),
            )
        )
    return variables[:3]


def infer_direction(name: str, text: str) -> str:
    lowered = f"{name} {text}".lower()
    if any(word in lowered for word in ("价格", "price", "hbm")):
        return "上行"
    if any(word in lowered for word in ("capex", "资本开支", "roi", "回报", "回落", "放缓")):
        return "下修"
    if any(word in lowered for word in ("利用率", "租赁", "云")):
        return "波动"
    return "上行 / 下行"


def infer_variable_reason(name: str) -> str:
    if any(keyword in name for keyword in ("GPU", "云", "租赁", "利用率")):
        return "影响算力供需判断"
    if any(keyword in name for keyword in ("HBM", "存储", "价格")):
        return "验证存储供需是否紧张"
    if any(keyword in name for keyword in ("capex", "资本开支")):
        return "验证 AI 投入回报压力"
    if any(keyword in name for keyword in ("电力", "MW", "GW", "液冷")):
        return "验证数据中心扩张约束"
    return "验证该信号能否继续形成主线"


def is_core_event(event: DigestEvent) -> bool:
    if event.evidence_level in {"D", "E"} and event.confidence_level == "低":
        return False
    if event.confidence_level == "低" and event.relevance_score < 95:
        return False
    if event.signal_status == "noise":
        return False
    return True


def is_readable_watch_event(event: DigestEvent) -> bool:
    title = event.title.strip().lower()
    url = event.canonical_url.lower().rstrip("/")
    blocked = (
        "semianalysis",
        "core research",
        "data product",
        "data products",
        "semianalysis-data-products",
        "chipbook",
        "events",
        "semianalysis-events",
        "compliance policies",
        "compliance polices",
    )
    if not title or title in blocked:
        return False
    if any(word in f"{title} {url}" for word in blocked):
        return False
    return event.signal_status != "noise"


def build_theme_changes(events: list[DigestEvent], themes: list[ThemeDefinition]) -> list[ThemeChange]:
    theme_by_id = {theme.id: theme for theme in themes}
    status_rank = {"escalating": 5, "reversing": 4, "weakening": 3, "continuing": 2, "new": 1, "noise": 0}
    history_available = THEME_HISTORY_PATH.exists()
    grouped: dict[str, list[DigestEvent]] = {}
    for event in events:
        for theme_id in event.theme_ids:
            grouped.setdefault(theme_id, []).append(event)
    changes = []
    for theme_id, theme_events in grouped.items():
        status = max((event.signal_status for event in theme_events), key=lambda value: status_rank.get(value, 0))
        if not history_available:
            status = "first_seen"
        event_titles = [event.title for event in theme_events[:3]]
        changes.append(
            ThemeChange(
                theme_id=theme_id,
                theme_name=theme_by_id.get(theme_id, ThemeDefinition(theme_id, theme_id)).name,
                signal_status=status,
                evidence_count=len(theme_events),
                high_weight_count=sum(1 for event in theme_events if event.confidence_level == "高"),
                today_event_titles=event_titles,
                new_event_titles=event_titles if not history_available else [event.title for event in theme_events if event.signal_status == "new"],
                history_available=history_available,
            )
        )
    return sorted(changes, key=lambda item: (-item.high_weight_count, -item.evidence_count, item.theme_name))[:6]


def build_follow_up_events(events: list[DigestEvent]) -> list[DigestEvent]:
    selected = sorted(
        [event for event in events if event.signal_status != "noise"],
        key=lambda event: (-event.relevance_score, event.title),
    )
    return selected[:5]


def _event_rank(event: DigestEvent) -> tuple[int, int, int]:
    confidence_score = {"高": 3, "中": 2, "低": 1}.get(event.confidence_level, 0)
    evidence_score = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}.get(event.evidence_level, 0)
    return (confidence_score, evidence_score, event.relevance_score)


def status_label(status: str) -> str:
    return SIGNAL_STATUS_LABELS.get(status, status)
