from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from job_agent.services.setup_service import SetupService


class FakeForm(dict):
    def __init__(self, values=None, lists=None):
        super().__init__(values or {})
        self._lists = lists or {}

    def getlist(self, key):
        return self._lists.get(key, [])


class SetupServiceTests(unittest.TestCase):
    def test_ensure_private_profile_copies_example(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "profile.example").mkdir()
            (root / "profile.example" / "canonical-cv.md").write_text("example", encoding="utf-8")

            SetupService(root).ensure_private_profile()

            self.assertEqual((root / "profile" / "canonical-cv.md").read_text(encoding="utf-8"), "example")

    def test_env_preserves_existing_keys_and_blank_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("ANTHROPIC_API_KEY=secret\nOTHER=keep\n", encoding="utf-8")

            SetupService(root).save_env_settings("", "claude sonnet", True)

            env = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("ANTHROPIC_API_KEY=secret", env)
            self.assertIn("OTHER=keep", env)
            self.assertIn('CLAUDE_MODEL="claude sonnet"', env)
            self.assertIn("CLAUDE_USE_BY_DEFAULT=true", env)
            with self.assertRaises(ValueError):
                SetupService(root).save_env_settings("bad\nkey", "model", True)

    def test_saves_contact_and_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)

            service.save_contact({"name": "Andreas Kielsgaard", "city": "Aarhus", "country": "Denmark"})
            contact = yaml.safe_load((root / "profile" / "contact.yaml").read_text(encoding="utf-8"))["contact"]
            self.assertEqual(contact["first_name"], "Andreas")
            self.assertEqual(contact["last_name"], "Kielsgaard")
            self.assertEqual(contact["city"], "Aarhus")

            service.save_preferences(
                available_from="Immediate",
                logistics="Relocation possible",
                current_base="Denmark",
                onsite_roles="Can relocate",
                preferred_regions="Denmark\nSweden",
                interests="Project coordination",
                minimum_digest_score=55,
            )
            preferences = yaml.safe_load((root / "profile" / "preferences.yaml").read_text(encoding="utf-8"))
            self.assertEqual(preferences["thresholds"]["minimum_digest_score"], 55)
            self.assertEqual(preferences["location_policy"]["preferred_regions"], ["Denmark", "Sweden"])

    def test_setup_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)

            service.save_setup_file("canonical_cv", "CV text")
            self.assertEqual((root / "profile" / "canonical-cv.md").read_text(encoding="utf-8"), "CV text")
            with self.assertRaises(KeyError):
                service.save_setup_file("unknown", "nope")

    def test_saves_match_engine_settings_and_scores_sandbox_form(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)
            form = FakeForm(
                {
                    "remote_policy": "required",
                    "permanent_policy": "exclude",
                    "permanent_penalty": "-30",
                    "technical_cap": "70",
                    "module_cap": "20",
                    "title": "SAP ABAP Consultant",
                    "location": "",
                    "remote": "Remote",
                    "rate": "",
                    "contract_duration": "",
                    "posted_date": "2026-05-04",
                    "workload": "",
                    "required_skills": "",
                    "required_modules": "",
                    "description": "ABAP contract role.",
                },
                {
                    "technical_rule_label": ["ABAP variants", ""],
                    "technical_rule_terms": ["abap, sap abap", ""],
                    "technical_rule_score": ["40", ""],
                    "technical_rule_mode": ["required", "bonus"],
                    "module_rule_label": ["QM"],
                    "module_rule_terms": ["qm"],
                    "module_rule_score": ["7"],
                    "module_rule_mode": ["bonus"],
                    "contract_rule_label": ["Contract"],
                    "contract_rule_terms": ["contract"],
                    "contract_rule_score": ["8"],
                    "contract_rule_mode": ["bonus"],
                },
            )

            settings = service.save_match_engine_settings_from_form(form)
            result = service.score_sandbox_form(form)

            preferences = yaml.safe_load((root / "profile" / "preferences.yaml").read_text(encoding="utf-8"))
            self.assertEqual(preferences["match_engine"]["remote_policy"], "required")
            self.assertEqual(settings["technical_keyword_groups"][0]["mode"], "required")
            self.assertEqual(result["category"], "exploratory")
            self.assertIn("ABAP variants", result["matched_keywords"])

    def test_auto_configure_profile_from_cv_applies_selected_ai_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)

            class FakeLlmService:
                def __init__(self, root):
                    pass

                def is_configured(self):
                    return True

                def complete(self, prompt, **kwargs):
                    assert "match_engine" in prompt
                    return type(
                        "Completion",
                        (),
                        {
                            "text": (
                                '{"canonical_cv":"# Consultant\\nABAP developer",'
                                '"match_engine":{"remote_policy":"required","permanent_policy":"exclude",'
                                '"technical_keyword_groups":[{"label":"ABAP variants",'
                                '"terms":["abap","abap development"],"score":40,"mode":"required"}]}}'
                            )
                        },
                    )()

            with patch("job_agent.services.setup_service.LlmService", FakeLlmService):
                result = service.auto_configure_profile_from_cv(
                    "ABAP developer CV text",
                    ["canonical_cv", "match_engine"],
                )

            self.assertEqual(result["applied"], ["canonical CV", "matchmaking settings"])
            self.assertIn("ABAP developer", (root / "profile" / "canonical-cv.md").read_text(encoding="utf-8"))
            preferences = yaml.safe_load((root / "profile" / "preferences.yaml").read_text(encoding="utf-8"))
            self.assertEqual(preferences["match_engine"]["remote_policy"], "required")
            self.assertEqual(preferences["match_engine"]["technical_keyword_groups"][0]["mode"], "required")

    def test_cv_profile_draft_does_not_write_profile_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)

            class FakeLlmService:
                def __init__(self, root):
                    pass

                def is_configured(self):
                    return True

                def complete(self, prompt, **kwargs):
                    return type(
                        "Completion",
                        (),
                        {
                            "text": (
                                '{"canonical_cv":"# Consultant\\nABAP developer",'
                                '"skills_yaml":{"skills":{"strongest":["ABAP","RAP"]}}}'
                            )
                        },
                    )()

            with patch("job_agent.services.setup_service.LlmService", FakeLlmService):
                draft = service.draft_profile_auto_configuration_from_cv(
                    "ABAP developer CV text",
                    ["canonical_cv", "skills"],
                )

            self.assertEqual(draft["targets"], ["canonical_cv", "skills"])
            self.assertEqual(draft["sections"][0]["label"], "CV narrative")
            self.assertFalse((root / "profile" / "canonical-cv.md").exists())

    def test_cv_profile_draft_repairs_invalid_json_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)
            calls: list[str] = []
            progress_events: list[str] = []

            class FakeLlmService:
                def __init__(self, root):
                    pass

                def is_configured(self):
                    return True

                def complete(self, prompt, **kwargs):
                    calls.append(kwargs["purpose"])
                    text = (
                        '{"canonical_cv":"# Consultant\\nABAP developer" '
                        '"skills_yaml":{"skills":{"strongest":["ABAP"]}}}'
                    )
                    if kwargs["purpose"] == "profile_auto_configuration_repair":
                        text = (
                            '{"canonical_cv":"# Consultant\\nABAP developer",'
                            '"skills_yaml":{"skills":{"strongest":["ABAP"]}}}'
                        )
                    return type("Completion", (), {"text": text})()

            with patch("job_agent.services.setup_service.LlmService", FakeLlmService):
                draft = service.draft_profile_auto_configuration_from_cv(
                    "ABAP developer CV text",
                    ["canonical_cv", "skills"],
                    progress_callback=lambda stage, message, percent: progress_events.append(stage),
                )

            self.assertEqual(calls, ["profile_auto_configuration", "profile_auto_configuration_repair"])
            self.assertIn("Repairing draft", progress_events)
            self.assertEqual(draft["data"]["skills_yaml"]["skills"]["strongest"], ["ABAP"])


if __name__ == "__main__":
    unittest.main()
