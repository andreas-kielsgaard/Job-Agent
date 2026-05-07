from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from job_agent.services.llm_service import LlmService
from job_agent.token_usage import TokenUsageStore


class LlmServiceTests(unittest.TestCase):
    def test_config_reads_env_file_fresh_and_prefers_it_for_local_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = root / ".env"
            env_path.write_text("ANTHROPIC_API_KEY=first-key\nCLAUDE_MODEL=first-model\n", encoding="utf-8")
            old_key = os.environ.get("ANTHROPIC_API_KEY")
            old_model = os.environ.get("CLAUDE_MODEL")
            os.environ["ANTHROPIC_API_KEY"] = "process-key"
            os.environ["CLAUDE_MODEL"] = "process-model"
            try:
                service = LlmService(root)

                self.assertEqual(service.api_key(), "first-key")
                self.assertEqual(service.model_name(), "first-model")

                env_path.write_text("ANTHROPIC_API_KEY=second-key\nCLAUDE_MODEL=second-model\n", encoding="utf-8")

                self.assertEqual(service.api_key(), "second-key")
                self.assertEqual(service.model_name(), "second-model")
            finally:
                if old_key is None:
                    os.environ.pop("ANTHROPIC_API_KEY", None)
                else:
                    os.environ["ANTHROPIC_API_KEY"] = old_key
                if old_model is None:
                    os.environ.pop("CLAUDE_MODEL", None)
                else:
                    os.environ["CLAUDE_MODEL"] = old_model

    def test_missing_api_key_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                with self.assertRaisesRegex(RuntimeError, "ANTHROPIC_API_KEY"):
                    LlmService(Path(directory)).complete("prompt", max_tokens=10, purpose="test")
            finally:
                if old_key is not None:
                    os.environ["ANTHROPIC_API_KEY"] = old_key

    def test_complete_tracks_token_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("ANTHROPIC_API_KEY=test-key\nCLAUDE_MODEL=test-model\n", encoding="utf-8")
            original = sys.modules.get("anthropic")
            sys.modules["anthropic"] = self._fake_anthropic_module()
            try:
                completion = LlmService(root).complete(
                    "prompt",
                    max_tokens=10,
                    purpose="ai_edit",
                    run_id="run-1",
                    associated_job_id="stable-1",
                )
            finally:
                if original is None:
                    sys.modules.pop("anthropic", None)
                else:
                    sys.modules["anthropic"] = original

            self.assertEqual(completion.text, "response text")
            records = TokenUsageStore(root).list_for_run("run-1")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].purpose, "ai_edit")
            self.assertEqual(records[0].input_tokens, 12)
            self.assertEqual(records[0].output_tokens, 4)
            self.assertEqual(records[0].associated_job_id, "stable-1")

    @staticmethod
    def _fake_anthropic_module() -> types.ModuleType:
        module = types.ModuleType("anthropic")

        class FakeMessages:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=12, output_tokens=4),
                    content=[SimpleNamespace(type="text", text="response text")],
                )

        class FakeAnthropic:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key
                self.messages = FakeMessages()

        module.Anthropic = FakeAnthropic
        return module


if __name__ == "__main__":
    unittest.main()
