from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from job_agent.services.setup_service import SetupService


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

    def test_sources_and_setup_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = SetupService(root)
            source_path = root / "sources" / "recruiting-sites.yaml"
            source_path.parent.mkdir(parents=True)
            source_path.write_text("sources:\n- name: Sample\n  enabled: true\n", encoding="utf-8")

            service.toggle_source(0, False)
            self.assertFalse(yaml.safe_load(source_path.read_text(encoding="utf-8"))["sources"][0]["enabled"])
            with self.assertRaises(IndexError):
                service.toggle_source(5, True)

            service.add_source(
                name="New", url="https://example.com", source_type="generic_html", keywords="ABAP\nRAP", enabled=True
            )
            sources = yaml.safe_load(source_path.read_text(encoding="utf-8"))["sources"]
            self.assertEqual(sources[-1]["keywords"], ["ABAP", "RAP"])

            with self.assertRaises(ValueError):
                service.add_source(
                    name="", url="https://example.com", source_type="generic_html", keywords="", enabled=True
                )
            with self.assertRaises(ValueError):
                service.add_source(name="Bad", url="", source_type="generic_html", keywords="", enabled=True)
            with self.assertRaises(ValueError):
                service.add_source(name="Bad", url="x", source_type="unknown", keywords="", enabled=True)

            service.save_setup_file("canonical_cv", "CV text")
            self.assertEqual((root / "profile" / "canonical-cv.md").read_text(encoding="utf-8"), "CV text")
            with self.assertRaises(KeyError):
                service.save_setup_file("unknown", "nope")


if __name__ == "__main__":
    unittest.main()
