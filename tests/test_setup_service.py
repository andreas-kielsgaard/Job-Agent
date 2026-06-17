from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from job_agent.email_models import GMAIL_READONLY_SCOPE
from job_agent.email_store import GmailCredentialStore, GmailSyncStateStore
from job_agent.services.connector_settings_service import ConnectorSettingsService
from job_agent.services.gmail_email_provider import GmailOAuthResult
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

    def test_connector_settings_save_canva_and_draft_only_email(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ConnectorSettingsService(root)

            settings = service.save_from_form(
                FakeForm(
                    {
                        "email_enabled": "on",
                        "email_provider": "generic",
                        "email_mode": "send",
                    }
                )
            )

            self.assertFalse(settings["canva"]["enabled"])
            self.assertEqual(settings["canva"]["oauth_status"], "not_connected")
            self.assertTrue(settings["email"]["enabled"])
            self.assertEqual(settings["email"]["provider"], "generic")
            self.assertEqual(settings["email"]["mode"], "draft_only")
            self.assertFalse(settings["email"]["sending_enabled"])
            self.assertIn("sending_enabled: false", (root / "connectors.yaml").read_text(encoding="utf-8"))

    def test_canva_oauth_flow_builds_url_and_saves_profile_from_callback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "CANVA_CLIENT_ID=canva-client\nCANVA_CLIENT_SECRET=canva-secret\n",
                encoding="utf-8",
            )
            service = ConnectorSettingsService(root)

            authorization_url = service.canva_authorization_url()
            stored = yaml.safe_load((root / "connectors.yaml").read_text(encoding="utf-8"))
            state = stored["canva"]["pending_oauth"]["state"]

            self.assertIn("https://www.canva.com/api/oauth/authorize?", authorization_url)
            self.assertIn("client_id=canva-client", authorization_url)
            self.assertIn("scope=profile%3Aread+design%3Acontent%3Awrite+design%3Ameta%3Aread", authorization_url)
            self.assertIn(f"state={state}", authorization_url)
            self.assertNotIn(stored["canva"]["pending_oauth"]["code_verifier"], authorization_url)

            class FakeResponse:
                def __init__(self, payload):
                    self.payload = payload

                def raise_for_status(self):
                    return None

                def json(self):
                    return self.payload

            def fake_post(url, data, auth, headers, timeout):
                self.assertEqual(url, "https://api.canva.com/rest/v1/oauth/token")
                self.assertEqual(data["grant_type"], "authorization_code")
                self.assertEqual(data["code"], "returned-code")
                self.assertEqual(auth, ("canva-client", "canva-secret"))
                self.assertEqual(timeout, 20)
                return FakeResponse(
                    {
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "token_type": "Bearer",
                        "expires_in": 14400,
                        "scope": "profile:read design:content:write design:meta:read",
                    }
                )

            def fake_get(url, headers, timeout):
                self.assertEqual(headers["Authorization"], "Bearer access-token")
                self.assertEqual(timeout, 20)
                if url.endswith("/users/me/profile"):
                    return FakeResponse({"profile": {"display_name": "Canva User"}})
                return FakeResponse({"team_user": {"user_id": "user-1", "team_id": "team-1"}})

            with (
                patch("job_agent.services.connector_settings_service.requests.post", fake_post),
                patch("job_agent.services.connector_settings_service.requests.get", fake_get),
            ):
                settings = service.complete_canva_oauth("returned-code", state)

            self.assertEqual(settings["canva"]["oauth_status"], "connected")
            self.assertEqual(settings["canva"]["connected_display_name"], "Canva User")
            self.assertEqual(settings["canva"]["connected_user_id"], "user-1")
            self.assertNotIn("access_token", settings["canva"])
            saved = yaml.safe_load((root / "connectors.yaml").read_text(encoding="utf-8"))
            self.assertEqual(saved["canva"]["access_token"], "access-token")
            self.assertEqual(saved["canva"]["pending_oauth"], {})

            with self.assertRaises(ValueError):
                service.complete_canva_oauth("returned-code", "wrong-state")

    def test_gmail_readonly_oauth_flow_stores_token_under_runtime_email(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "GMAIL_CLIENT_ID=gmail-client\nGMAIL_CLIENT_SECRET=gmail-secret\n",
                encoding="utf-8",
            )
            service = ConnectorSettingsService(root)
            test_case = self

            class FakeGmailOAuthClient:
                def __init__(self, config):
                    self.config = config

                def authorization_url(self, state):
                    test_case.assertEqual(self.config["scopes"], (GMAIL_READONLY_SCOPE,))
                    return f"https://accounts.example/auth?state={state}"

                def complete_oauth(self, code, state):
                    test_case.assertEqual(code, "returned-code")
                    return GmailOAuthResult(
                        credentials_json='{"token": "gmail-token"}',
                        account_email="me@example.com",
                        history_id="123",
                    )

            with patch("job_agent.services.connector_settings_service.GmailOAuthClient", FakeGmailOAuthClient):
                authorization_url = service.gmail_authorization_url()
                stored = yaml.safe_load((root / "connectors.yaml").read_text(encoding="utf-8"))
                state = stored["email"]["pending_oauth"]["state"]
                self.assertIn(f"state={state}", authorization_url)
                settings = service.complete_gmail_oauth("returned-code", state)

            self.assertEqual(settings["email"]["oauth_status"], "connected")
            self.assertEqual(settings["email"]["connected_email"], "me@example.com")
            self.assertFalse(settings["email"]["sending_enabled"])
            self.assertNotIn("token", settings["email"])
            self.assertEqual(GmailCredentialStore(root).read_text(), '{"token": "gmail-token"}')
            self.assertEqual(GmailSyncStateStore(root).get().last_history_id, "123")

            service.disconnect_gmail()
            self.assertFalse(GmailCredentialStore(root).exists())

    def test_saves_contact_and_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)

            service.save_contact(
                {
                    "name": "Andreas Kielsgaard",
                    "city": "Aarhus",
                    "country": "Denmark",
                    "professional_links": [
                        {"label": "Portfolio", "url": "https://andreas.example"},
                        {"label": "", "url": "not-a-url"},
                    ],
                }
            )
            contact = yaml.safe_load((root / "profile" / "contact.yaml").read_text(encoding="utf-8"))["contact"]
            self.assertEqual(contact["first_name"], "Andreas")
            self.assertEqual(contact["last_name"], "Kielsgaard")
            self.assertEqual(contact["city"], "Aarhus")
            self.assertEqual(contact["professional_links"], [{"label": "Portfolio", "url": "https://andreas.example"}])

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

            service.save_runtime_settings(max_parallel_sources=12)
            preferences = yaml.safe_load((root / "profile" / "preferences.yaml").read_text(encoding="utf-8"))
            self.assertEqual(preferences["runtime"]["max_parallel_sources"], 12)

    def test_setup_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)

            service.save_setup_file("canonical_cv", "CV text")
            self.assertEqual((root / "profile" / "canonical-cv.md").read_text(encoding="utf-8"), "CV text")
            self.assertIn("application_examples", service.setup_files())
            with self.assertRaises(KeyError):
                service.save_setup_file("unknown", "nope")

    def test_saves_skill_matrix_and_preserves_unrelated_match_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            (profile / "skills.yaml").write_text(
                "skills:\n"
                "  strongest:\n"
                "    - SAP ABAP\n"
                "  modules:\n"
                "    strong:\n"
                "      - QM\n"
                "    experienced: []\n"
                "    adjacent: []\n"
                "  caveats:\n"
                "    fiori: Old caveat\n"
                "target_roles:\n"
                "  high_match:\n"
                "    - SAP Developer\n",
                encoding="utf-8",
            )
            (profile / "preferences.yaml").write_text(
                "match_engine:\n"
                "  technical_keyword_groups:\n"
                "    - label: SAP ABAP\n"
                "      terms: [abap]\n"
                "      score: 22\n"
                "      mode: bonus\n"
                "    - label: Manual scoring rule\n"
                "      terms: [keep me]\n"
                "      score: 5\n"
                "      mode: bonus\n"
                "  module_keyword_groups:\n"
                "    - label: QM\n"
                "      terms: [qm]\n"
                "      score: 7\n"
                "      mode: bonus\n"
                "match_review:\n"
                "  caveat_rules:\n"
                "    - id: caveat_fiori\n"
                "      label: Fiori\n"
                "      terms: [fiori]\n"
                "      caveat_key: fiori\n"
                "      ai_review: true\n",
                encoding="utf-8",
            )

            SetupService(root).save_skill_matrix_from_form(
                FakeForm(
                    lists={
                        "skill_name": ["SAP ABAP", "RAP"],
                        "skill_terms": ["abap\nsap abap", "rap"],
                        "skill_score": ["24", "12"],
                        "skill_mode": ["bonus", "required"],
                        "module_lane": ["strong"],
                        "module_name": ["QM"],
                        "module_terms": ["qm\nquality management"],
                        "module_score": ["8"],
                        "module_mode": ["bonus"],
                        "role_bucket": ["high_match"],
                        "role_name": ["SAP Developer"],
                        "role_aliases": ["ABAP consultant"],
                        "caveat_key": ["fiori"],
                        "caveat_text": ["Backend Fiori experience, not pure UI5."],
                        "caveat_terms": ["fiori\nui5"],
                        "caveat_ai_review": ["true"],
                    }
                )
            )

            skills = yaml.safe_load((profile / "skills.yaml").read_text(encoding="utf-8"))
            preferences = yaml.safe_load((profile / "preferences.yaml").read_text(encoding="utf-8"))
            self.assertEqual(skills["skills"]["strongest"], ["SAP ABAP", "RAP"])
            self.assertEqual(skills["target_role_aliases"]["SAP Developer"], ["ABAP consultant"])
            technical_labels = [rule["label"] for rule in preferences["match_engine"]["technical_keyword_groups"]]
            self.assertIn("Manual scoring rule", technical_labels)
            self.assertIn("RAP", technical_labels)
            self.assertEqual(preferences["match_review"]["caveat_rules"][0]["caveat_key"], "fiori")

    def test_simple_skill_matrix_save_preserves_advanced_matching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            (profile / "skills.yaml").write_text(
                "skills:\n"
                "  strongest:\n"
                "    - SAP ABAP\n"
                "  modules:\n"
                "    strong:\n"
                "      - QM\n"
                "    experienced: []\n"
                "    adjacent: []\n"
                "  caveats:\n"
                "    fiori: Backend Fiori experience, not pure UI5.\n"
                "target_roles:\n"
                "  high_match:\n"
                "    - SAP Developer\n"
                "  exploratory_match: []\n"
                "  lower_match: []\n"
                "target_role_aliases:\n"
                "  SAP Developer:\n"
                "    - ABAP consultant\n",
                encoding="utf-8",
            )
            (profile / "preferences.yaml").write_text(
                "match_engine:\n"
                "  technical_keyword_groups:\n"
                "    - label: SAP ABAP\n"
                "      terms: [abap]\n"
                "      score: 22\n"
                "      mode: required\n"
                "    - label: Manual scoring rule\n"
                "      terms: [keep me]\n"
                "      score: 5\n"
                "      mode: bonus\n"
                "  module_keyword_groups:\n"
                "    - label: QM\n"
                "      terms: [qm]\n"
                "      score: 7\n"
                "      mode: bonus\n"
                "match_review:\n"
                "  caveat_rules:\n"
                "    - id: caveat_fiori\n"
                "      label: Fiori\n"
                "      terms: [fiori]\n"
                "      caveat_key: fiori\n"
                "      ai_review: true\n",
                encoding="utf-8",
            )
            before_preferences = yaml.safe_load((profile / "preferences.yaml").read_text(encoding="utf-8"))

            SetupService(root).save_skill_matrix_from_form(
                FakeForm(
                    lists={
                        "skill_name": ["SAP ABAP", "CDS Views"],
                        "module_lane": ["strong"],
                        "module_name": ["QM"],
                        "role_bucket": ["high_match"],
                        "role_name": ["SAP Developer"],
                        "caveat_key": ["fiori"],
                        "caveat_text": ["Backend Fiori experience, not pure UI5."],
                    }
                )
            )

            skills = yaml.safe_load((profile / "skills.yaml").read_text(encoding="utf-8"))
            preferences = yaml.safe_load((profile / "preferences.yaml").read_text(encoding="utf-8"))
            self.assertEqual(skills["skills"]["strongest"], ["SAP ABAP", "CDS Views"])
            self.assertEqual(skills["target_role_aliases"]["SAP Developer"], ["ABAP consultant"])
            self.assertEqual(preferences, before_preferences)

    def test_saves_case_studies_application_examples_and_ai_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            service = SetupService(root)

            service.save_case_studies_from_form(
                FakeForm(
                    lists={
                        "case_company": ["LEGO"],
                        "case_role": ["Developer"],
                        "case_highlights": ["Built service\nImproved flow"],
                        "case_keywords": ["ABAP"],
                        "case_linked_skills": ["SAP ABAP"],
                        "case_linked_modules": ["QM"],
                        "case_linked_roles": ["Developer"],
                    }
                )
            )
            experience = yaml.safe_load((profile / "experience.yaml").read_text(encoding="utf-8"))
            self.assertEqual(experience["experience"][0]["linked_skills"], ["SAP ABAP"])

            service.save_application_examples_from_form(
                FakeForm(
                    lists={
                        "example_id": [""],
                        "example_label": ["Concise ABAP note"],
                        "example_application_text": ["Human edited text"],
                        "example_job_title": ["ABAP Consultant"],
                        "example_company": ["Recruiter"],
                        "example_url": ["https://example.com"],
                        "example_linked_skills": ["SAP ABAP"],
                        "example_linked_modules": ["QM"],
                        "example_linked_roles": ["Developer"],
                        "example_notes": ["Good tone"],
                    }
                )
            )
            examples = yaml.safe_load((profile / "application-examples.yaml").read_text(encoding="utf-8"))
            self.assertEqual(examples["application_examples"][0]["application_text"], "Human edited text")
            self.assertTrue(examples["application_examples"][0]["id"])

            service.save_ai_policy_from_form(
                FakeForm(
                    {
                        "ai_min_score": "40",
                        "language_penalty": "-20",
                        "min_core_matches": "2",
                        "high_rate_threshold": "900",
                    },
                    {
                        "evaluate_category": ["strong", "exploratory"],
                    },
                )
            )
            preferences = yaml.safe_load((profile / "preferences.yaml").read_text(encoding="utf-8"))
            self.assertEqual(preferences["ai_review_policy"]["min_score"], 40)
            self.assertEqual(preferences["ai_review_policy"]["evaluate_categories"], ["strong", "exploratory"])
            self.assertEqual(preferences["language_policy"]["penalty"], -20)

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

    def test_auto_configure_profile_from_cv_applies_contact_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)

            class FakeLlmService:
                def __init__(self, root):
                    pass

                def is_configured(self):
                    return True

                def complete(self, prompt, **kwargs):
                    assert "Requested sections: contact" in prompt
                    return type(
                        "Completion",
                        (),
                        {
                            "text": (
                                '{"contact_yaml":{"contact":{"name":"Ada Lovelace",'
                                '"title":"Research Engineer","email":"ada@example.com",'
                                '"professional_links":[{"label":"Portfolio","url":"https://ada.example"}]}}}'
                            )
                        },
                    )()

            with patch("job_agent.services.setup_service.LlmService", FakeLlmService):
                result = service.auto_configure_profile_from_cv("Ada CV text", ["contact"])

            self.assertEqual(result["applied"], ["profile basics"])
            contact = yaml.safe_load((root / "profile" / "contact.yaml").read_text(encoding="utf-8"))["contact"]
            self.assertEqual(contact["name"], "Ada Lovelace")
            self.assertEqual(contact["first_name"], "Ada")
            self.assertEqual(contact["professional_links"][0]["url"], "https://ada.example")

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

    def test_cv_profile_draft_passes_one_shot_model_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)
            calls: list[dict] = []

            class FakeLlmService:
                def __init__(self, root):
                    pass

                def is_configured(self):
                    return True

                def complete(self, prompt, **kwargs):
                    calls.append(kwargs)
                    return type(
                        "Completion",
                        (),
                        {"text": '{"canonical_cv":"# Consultant\\nABAP developer"}'},
                    )()

            with patch("job_agent.services.setup_service.LlmService", FakeLlmService):
                draft = service.draft_profile_auto_configuration_from_cv(
                    "ABAP developer CV text",
                    ["canonical_cv"],
                    llm_model="claude-opus-4-8",
                )

            self.assertEqual(draft["targets"], ["canonical_cv"])
            self.assertEqual(calls[0]["model"], "claude-opus-4-8")

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
