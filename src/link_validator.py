from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class LinkValidationResult:
    link_status: str
    final_url: str | None = None
    link_error: str | None = None
    link_checked_at: datetime | None = None


def validate_link(url: str, timeout: float = 8.0) -> LinkValidationResult:
    checked_at = datetime.now(timezone.utc)
    if not urlparse(url).scheme:
        return LinkValidationResult("invalid", None, "missing_scheme", checked_at)
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,text/plain,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Connection": "close",
            },
        ) as client:
            response = client.get(url)
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
        return LinkValidationResult("unknown", url, type(exc).__name__, checked_at)
    except Exception as exc:
        return LinkValidationResult("unknown", url, type(exc).__name__, checked_at)

    final_url = str(response.url)
    if response.status_code in {401, 403, 404, 410, 500} or response.status_code >= 500:
        return LinkValidationResult("invalid", final_url, f"http_{response.status_code}", checked_at)
    if response.status_code != 200:
        return LinkValidationResult("unknown", final_url, f"http_{response.status_code}", checked_at)

    content_type = response.headers.get("content-type", "").lower()
    if content_type and not any(kind in content_type for kind in ("html", "text", "xml")):
        return LinkValidationResult("invalid", final_url, f"unsupported_content_type:{content_type}", checked_at)
    if _looks_like_invalid_redirect(url, final_url, response.text[:2000]):
        return LinkValidationResult("invalid", final_url, "redirected_to_non_article", checked_at)
    return LinkValidationResult("valid", final_url, None, checked_at)


def _looks_like_invalid_redirect(original_url: str, final_url: str, html: str) -> bool:
    original = urlparse(original_url)
    final = urlparse(final_url)
    final_path = final.path.rstrip("/")
    original_path = original.path.rstrip("/")
    if original_path not in {"", "/"} and final_path in {"", "/"}:
        return True
    lowered = f"{final_url}\n{html}".lower()
    if len(html.strip()) < 120:
        return True
    if any(
        marker in lowered
        for marker in (
            "login",
            "signin",
            "sign-in",
            "登录",
            "请登录",
            "404",
            "not found",
            "页面不存在",
            "error page",
            "access denied",
            "forbidden",
            "页面错误",
        )
    ):
        return True
    return False
