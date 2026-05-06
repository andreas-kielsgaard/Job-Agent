from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from job_agent.services.llm_service import LlmService
from job_agent.token_usage import TokenUsageStore


class LlmServiceTests(unittest.TestCase):
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
