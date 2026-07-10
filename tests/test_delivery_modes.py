from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import ARTIFACTS_DIR, build_digest_id, render_preview_email, send_digest_once, send_test_digest
from src.models import DailyDigest
from src.storage import SentDigestStore


class DeliveryModeWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (PROJECT_ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")

    def test_workflow_dispatch_defaults_to_preview(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn('default: "preview"', self.workflow)
        self.assertIn("- preview", self.workflow)
        self.assertIn("- test-email", self.workflow)
        self.assertIn("- production", self.workflow)
        self.assertNotIn("\npush:", self.workflow)

    def test_preview_mode_does_not_send_email(self) -> None:
        self.assertIn("name: Preview only", self.workflow)
        self.assertIn("github.event.inputs.mode == 'preview'", self.workflow)
        self.assertIn("python3 -m compileall main.py src scripts", self.workflow)
        self.assertIn("python3 -m unittest", self.workflow)
        self.assertIn("python3 main.py --preview-email", self.workflow)
        preview_block = self.workflow.split("name: Preview only", 1)[1].split("name: Send test email", 1)[0]
        self.assertNotIn("SMTP_HOST", preview_block)
        self.assertNotIn("python main.py\n", preview_block)

    def test_schedule_and_manual_production_send_formal_digest(self) -> None:
        self.assertIn('schedule:\n    - cron: "0 1 * * *"', self.workflow)
        self.assertIn("name: Send scheduled digest", self.workflow)
        self.assertIn("github.event_name == 'schedule'", self.workflow)
        self.assertIn("name: Send manual production digest", self.workflow)
        self.assertIn("github.event.inputs.mode == 'production'", self.workflow)
        self.assertIn("run: python main.py", self.workflow)

    def test_test_email_mode_is_separate_from_production(self) -> None:
        self.assertIn("name: Send test email", self.workflow)
        self.assertIn("github.event.inputs.mode == 'test-email'", self.workflow)
        self.assertIn("run: python main.py --test-email", self.workflow)
        self.assertIn("TEST_EMAIL_TO", self.workflow)

    def test_concurrency_guard_exists(self) -> None:
        self.assertIn("concurrency:", self.workflow)
        self.assertIn("group: ai-news-agent-daily-${{ github.ref }}", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)


class SentDigestGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_digest_events.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.digest = DailyDigest.model_validate(data["digest"])
        self.subject = "AI 投研情报日报｜2026-07-10｜HBM供需"

    def test_same_digest_id_is_not_sent_twice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SentDigestStore(Path(temp_dir) / "sent_digests.json")
            digest_id = build_digest_id("2026-07-10", self.subject, self.digest)
            with patch("main.send_email") as send_mock:
                first_sent = send_digest_once(self.subject, "text", "<html></html>", self.digest, "2026-07-10", store)
                second_sent = send_digest_once(self.subject, "text", "<html></html>", self.digest, "2026-07-10", store)
            self.assertTrue(first_sent)
            self.assertFalse(second_sent)
            self.assertEqual(send_mock.call_count, 1)
            self.assertTrue(store.has_sent(digest_id))

    def test_sent_record_is_written_only_after_successful_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SentDigestStore(Path(temp_dir) / "sent_digests.json")
            digest_id = build_digest_id("2026-07-10", self.subject, self.digest)
            with patch("main.send_email", side_effect=RuntimeError("smtp failed")):
                with self.assertRaises(RuntimeError):
                    send_digest_once(self.subject, "text", "<html></html>", self.digest, "2026-07-10", store)
            self.assertFalse(store.has_sent(digest_id))

    def test_preview_does_not_write_sent_digests(self) -> None:
        sent_path = ARTIFACTS_DIR / "sent_digests.json"
        before = sent_path.read_text(encoding="utf-8") if sent_path.exists() else None
        render_preview_email()
        after = sent_path.read_text(encoding="utf-8") if sent_path.exists() else None
        self.assertEqual(before, after)

    def test_test_email_uses_test_recipient_and_does_not_mark_production_sent(self) -> None:
        with patch.dict(os.environ, {"TEST_EMAIL_TO": "test@example.com"}):
            with patch("main.send_email") as send_mock:
                send_test_digest(self.subject, "text", "<html></html>")
        send_mock.assert_called_once()
        args, kwargs = send_mock.call_args
        self.assertTrue(args[0].startswith("[TEST] "))
        self.assertEqual(kwargs["to_email"], "test@example.com")


if __name__ == "__main__":
    unittest.main()
