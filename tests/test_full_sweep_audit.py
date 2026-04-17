import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import full_sweep_audit


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeAsyncSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        return self._response


class FullSweepAuditTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_bearer_token_supports_nested_fields(self):
        self.assertEqual(
            full_sweep_audit.extract_bearer_token({"tokens": {"access_token": "nested-token"}}),
            "nested-token",
        )
        self.assertEqual(
            full_sweep_audit.extract_bearer_token({"access_token": "Bearer direct-token"}),
            "direct-token",
        )

    def test_extract_output_text_reads_responses_payload(self):
        payload = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": "ok"},
                        {"type": "text", "text": "done"},
                    ]
                }
            ]
        }
        self.assertEqual(full_sweep_audit.extract_output_text(payload), "ok\ndone")

    async def test_verify_credential_marks_success_on_http_200_with_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "good.json"
            path.write_text(json.dumps({"access_token": "token-1"}), encoding="utf-8")
            session = FakeAsyncSession(
                FakeResponse(
                    status_code=200,
                    payload={"output": [{"content": [{"type": "output_text", "text": "ok"}]}]},
                )
            )

            outcome = await full_sweep_audit.verify_credential(
                path,
                session,
                full_sweep_audit.SweepConfig(),
            )

            self.assertTrue(outcome.usable)
            self.assertEqual(outcome.output_text, "ok")
            self.assertEqual(
                session.calls[0]["kwargs"]["headers"]["Authorization"],
                "Bearer token-1",
            )

    async def test_process_file_moves_verified_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"
            log_path = root / "progress.log"
            source_dir.mkdir()
            path = source_dir / "good.json"
            path.write_text(json.dumps({"access_token": "token-1"}), encoding="utf-8")
            config = full_sweep_audit.SweepConfig(
                source_dir=source_dir,
                target_dir=target_dir,
                progress_log=log_path,
            )
            progress = full_sweep_audit.ProgressTracker(total=1, log_path=log_path)
            await progress.initialize()
            session = FakeAsyncSession(
                FakeResponse(
                    status_code=200,
                    payload={"output": [{"content": [{"type": "output_text", "text": "ok"}]}]},
                )
            )

            await full_sweep_audit.process_file(
                path,
                session,
                config,
                progress,
                asyncio.Semaphore(1),
            )

            self.assertFalse(path.exists())
            self.assertTrue((target_dir / "good.json").exists())

    async def test_process_file_deletes_invalid_token_and_logs_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            log_path = root / "progress.log"
            source_dir.mkdir()
            path = source_dir / "bad.json"
            path.write_text(json.dumps({"access_token": "token-2"}), encoding="utf-8")
            config = full_sweep_audit.SweepConfig(
                source_dir=source_dir,
                target_dir=root / "target",
                progress_log=log_path,
            )
            progress = full_sweep_audit.ProgressTracker(total=1, log_path=log_path)
            await progress.initialize()
            session = FakeAsyncSession(
                FakeResponse(status_code=401, payload={"error": "nope"}, text='{"error":"nope"}')
            )

            await full_sweep_audit.process_file(
                path,
                session,
                config,
                progress,
                asyncio.Semaphore(1),
            )

            self.assertFalse(path.exists())
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("failure=1", log_text)
            self.assertIn("HTTP 401", log_text)
