from __future__ import annotations

import os
import smtplib
import logging
from email.message import EmailMessage

from .models import DailyDigest

logger = logging.getLogger(__name__)


def render_email_text(digest: DailyDigest) -> str:
    lines: list[str] = [digest.subject, "", "开头摘要", digest.opening_summary.strip()]
    if digest.trend:
        lines.extend(["", digest.trend.strip()])
    lines.extend(["", "----", "", "今日最重要的资讯"])

    if not digest.items:
        lines.extend(["", "指定来源暂未抓取到可用于生成日报的新内容。"])
        return "\n".join(lines)

    for item in digest.items:
        lines.extend(
            [
                "",
                f"【{item.importance}】{item.title}",
                f"核心事实：{item.core_fact}",
                f"重要意义：{item.important_meaning}",
                "关键点：",
            ]
        )
        lines.extend(f"- {point}" for point in item.key_points[:3])
        lines.extend([f"链接：{item.url}", "", "----"])
    return "\n".join(lines).strip()


def send_email(subject: str, body: str) -> None:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing email environment variables: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["EMAIL_FROM"]
    message["To"] = os.environ["EMAIL_TO"]
    message.set_content(body, subtype="plain", charset="utf-8")

    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
        elif port == 587:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(message)
        logger.info("Email sent successfully")
    except smtplib.SMTPException as exc:
        logger.error("SMTP error while sending email: %s", exc)
        raise
    except OSError as exc:
        logger.error("SMTP connection error while sending email: %s", exc)
        raise
