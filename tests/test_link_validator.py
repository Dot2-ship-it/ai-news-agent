from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.link_validator import _looks_like_invalid_redirect, validate_link


class LinkValidatorTest(unittest.TestCase):
    def test_missing_scheme_is_invalid(self) -> None:
        result = validate_link("www.qbitai.com/2026/07/446778.html")
        self.assertEqual(result.link_status, "invalid")
        self.assertEqual(result.link_error, "missing_scheme")

    def test_redirect_to_homepage_is_invalid(self) -> None:
        self.assertTrue(
            _looks_like_invalid_redirect(
                "https://www.qbitai.com/2026/07/446778.html",
                "https://www.qbitai.com/",
                "<html><body>量子位首页</body></html>",
            )
        )

    def test_login_or_empty_error_pages_are_invalid(self) -> None:
        self.assertTrue(
            _looks_like_invalid_redirect(
                "https://example.com/article",
                "https://example.com/login",
                "<html><body>请登录后查看</body></html>",
            )
        )
        self.assertTrue(
            _looks_like_invalid_redirect(
                "https://example.com/article",
                "https://example.com/article",
                "   ",
            )
        )


if __name__ == "__main__":
    unittest.main()
