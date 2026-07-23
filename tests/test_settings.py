import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anistream.services.settings import SettingsStore


class ProviderSettingsTests(unittest.TestCase):
    def test_every_provider_is_enabled_by_default_including_future_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder) / "settings.json")

            self.assertTrue(store.provider_enabled("anime_sama"))
            self.assertTrue(store.provider_enabled("future_site"))

    def test_disabled_provider_choice_is_persisted_and_can_be_reenabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            store = SettingsStore(path)
            store.set_provider_enabled("french_stream", False)

            loaded = SettingsStore(path)
            self.assertFalse(loaded.provider_enabled("french_stream"))
            self.assertTrue(loaded.provider_enabled("anime_sama"))

            loaded.set_provider_enabled("french_stream", True)
            self.assertTrue(SettingsStore(path).provider_enabled("french_stream"))

    def test_provider_settings_accept_future_provider_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            store = SettingsStore(path)
            store.set_provider_settings("future_site", {"token": "local-only"})

            loaded = SettingsStore(path)
            self.assertEqual(loaded.provider_settings("future_site"), {"token": "local-only"})
            self.assertIn("future_site", loaded.as_dict()["providers"])

    def test_legacy_anime_sama_settings_are_migrated_in_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(
                json.dumps({"anime_sama": {"user_agent": "legacy", "cf_clearance": "private"}}),
                encoding="utf-8",
            )

            store = SettingsStore(path)

            self.assertEqual(
                store.provider_settings("anime_sama"),
                {"user_agent": "legacy", "cf_clearance": "private"},
            )


class ExecutableDiscoveryTests(unittest.TestCase):
    def test_automatic_mpv_discovery_rejects_project_binaries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            project_mpv = project / "mpv.exe"
            project_mpv.write_bytes(b"untrusted")
            installed_mpv = root / "installed" / "mpv.exe"
            installed_mpv.parent.mkdir()
            installed_mpv.write_bytes(b"trusted")
            store = SettingsStore(root / "settings.json")

            with (
                patch("anistream.services.settings.project_root", return_value=project),
                patch("anistream.services.settings.shutil.which", return_value=str(project_mpv)),
                patch.object(
                    SettingsStore,
                    "_platform_executable_candidates",
                    return_value=(installed_mpv,),
                ),
            ):
                self.assertEqual(store.executable("mpv_path", "mpv"), str(installed_mpv.resolve()))

    def test_explicitly_configured_project_binary_remains_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"
            project.mkdir()
            project_mpv = project / "mpv.exe"
            project_mpv.write_bytes(b"user-selected")
            store = SettingsStore(root / "settings.json")
            store.set("mpv_path", str(project_mpv))

            with patch("anistream.services.settings.project_root", return_value=project):
                self.assertEqual(store.executable("mpv_path", "mpv"), str(project_mpv.resolve()))


if __name__ == "__main__":
    unittest.main()
