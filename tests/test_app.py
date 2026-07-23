import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anistream.app import Application
from anistream.errors import ProviderError, ResolverError
from anistream.models import Catalogue, Episode, MediaLanguage, ResolvedMedia
from anistream.services.local_library import LocalLibraryEntry
from anistream.services.source_planner import SourcePlan


class FakeHistory:
    def __init__(self, item):
        self.item = item

    def all(self):
        return [dict(self.item)]


class FakeCli:
    def __init__(self, item, completed_action=None):
        self.item = item
        self.completed_action = completed_action
        self.console = self
        self.messages = []

    def choose_history_entry(self, entries):
        return entries[0]

    def status(self, _message):
        return nullcontext()

    def show_catalogue(self, _catalogue, _history):
        pass

    def clear_screen(self):
        pass

    def pause(self, _message=""):
        pass

    def completed_history_action(self, _item):
        return self.completed_action

    def info(self, message):
        self.messages.append(message)

    def error(self, message):
        self.messages.append(message)

    def warning(self, message):
        self.messages.append(message)


class FakeProvider:
    id = "site"
    name = "Site"

    def __init__(self, catalogue):
        self._catalogue = catalogue

    def catalogue(self, _url):
        return self._catalogue


class FakeProviders:
    def __init__(self, provider):
        self.provider = provider

    def get(self, provider_id):
        return self.provider if provider_id == self.provider.id else None


def catalogue():
    return Catalogue(
        "site",
        "Site",
        "Title",
        "https://site/title/season/en/",
        "Season 1",
        MediaLanguage("en", "EN"),
        tuple(Episode(number, ()) for number in range(1, 13)),
    )


class ContinueWatchingTests(unittest.TestCase):
    def application(self, item, completed_action=None):
        app = Application.__new__(Application)
        app.history = FakeHistory(item)
        app.cli = FakeCli(item, completed_action)
        app.providers = FakeProviders(FakeProvider(catalogue()))
        app._sync_history = lambda _catalogue: dict(item)
        return app

    def test_in_progress_title_resumes_without_episode_prompt(self):
        item = {
            "provider_id": "site",
            "catalogue_url": "https://site/title/season/en/",
            "title": "Title",
            "status": "in_progress",
            "current_episode": 3,
        }
        app = self.application(item)
        calls = []
        app._watch = lambda _catalogue, **kwargs: calls.append(kwargs)

        app._continue_watching()

        self.assertEqual(calls, [{"start_episode": 3}])

    def test_completed_title_restarts_only_after_explicit_action(self):
        item = {
            "provider_id": "site",
            "catalogue_url": "https://site/title/season/en/",
            "title": "Title",
            "status": "completed",
            "current_episode": 12,
        }
        app = self.application(item, completed_action="restart")
        calls = []
        app._watch = lambda _catalogue, **kwargs: calls.append(kwargs)

        app._continue_watching()

        self.assertEqual(calls, [{"start_episode": 1}])


class MainLoopTests(unittest.TestCase):
    class Cli:
        def __init__(self):
            self.choices = iter(["5", "q"])
            self.main_screen_calls = 0
            self.settings_calls = 0

        def main_screen(self):
            self.main_screen_calls += 1

        def main_choice(self):
            return next(self.choices)

        def settings_menu(self, _sources=()):
            self.settings_calls += 1

    def test_header_is_redrawn_when_returning_to_main_menu(self):
        app = Application.__new__(Application)
        app.cli = self.Cli()
        app.available_providers = ()
        app.settings = Mock()
        app._refresh_providers = Mock()

        self.assertEqual(app.run(), 0)
        self.assertEqual(app.cli.settings_calls, 1)
        self.assertEqual(app.cli.main_screen_calls, 2)
        app._refresh_providers.assert_called_once_with()

    def test_main_menu_routes_search_local_link_and_settings_in_order(self):
        app = Application.__new__(Application)
        app.cli = self.Cli()
        app.available_providers = ()
        app.settings = Mock()
        app._refresh_providers = Mock()
        app.cli.choices = iter(["2", "3", "4", "5", "q"])
        app._from_search = Mock(return_value=None)
        app._local_library = Mock()
        app._from_link = Mock(return_value=None)

        self.assertEqual(app.run(), 0)

        app._from_search.assert_called_once_with()
        app._local_library.assert_called_once_with()
        app._from_link.assert_called_once_with()
        self.assertEqual(app.cli.settings_calls, 1)


class EntryScreenTests(unittest.TestCase):
    def test_search_uses_a_clean_dedicated_input_screen(self):
        app = Application.__new__(Application)
        app.cli = Mock()
        app.cli.ask.return_value = ""

        self.assertIsNone(app._from_search())

        app.cli.input_screen.assert_called_once_with(
            "Search",
            "Search every enabled site by title.",
        )

    def test_link_uses_a_clean_dedicated_input_screen(self):
        app = Application.__new__(Application)
        app.cli = Mock()
        app.cli.ask.return_value = "https://unsupported.example/title"
        app.providers = Mock()
        app.providers.detect.return_value = None
        app.providers.names.return_value = "Site"
        app.available_providers = ()

        self.assertIsNone(app._from_link())

        app.cli.input_screen.assert_called_once_with(
            "Open a link",
            "Paste a catalogue URL from any enabled site.",
        )

    def test_link_explains_when_its_source_is_disabled(self):
        provider = Mock()
        provider.name = "Anime-Sama"
        provider.matches.return_value = True
        app = Application.__new__(Application)
        app.cli = Mock()
        app.cli.ask.return_value = "https://anime-sama.example/title"
        app.providers = Mock()
        app.providers.detect.return_value = None
        app.available_providers = (provider,)

        self.assertIsNone(app._from_link())

        app.cli.error.assert_called_once_with(
            "Anime-Sama is disabled. Enable it in Settings > Sources to open this link."
        )
        app.cli.pause.assert_called_once_with()

    def test_search_stops_cleanly_when_every_source_is_disabled(self):
        app = Application.__new__(Application)
        app.cli = Mock()
        app.cli.ask.return_value = "title"
        app.providers = Mock()
        app.providers.providers = ()

        self.assertIsNone(app._from_search())

        app.providers.search.assert_not_called()
        app.cli.warning.assert_called_once_with(
            "No sources are enabled. Enable at least one in Settings > Sources."
        )
        app.cli.pause.assert_called_once_with()


class ProviderSelectionTests(unittest.TestCase):
    def test_registry_is_rebuilt_from_saved_provider_choices(self):
        enabled = SimpleNamespace(id="enabled")
        disabled = SimpleNamespace(id="disabled")
        app = Application.__new__(Application)
        app.available_providers = (enabled, disabled)
        app.settings = Mock()
        app.settings.provider_enabled.side_effect = lambda provider_id: provider_id == "enabled"

        app._refresh_providers()

        self.assertEqual(app.providers.providers, (enabled,))


class DownloadCoverageTests(unittest.TestCase):
    @patch("anistream.app.DownloadManager")
    def test_declining_incomplete_coverage_stops_before_transfer(self, manager):
        app = Application.__new__(Application)
        app.cli = Mock()
        app.cli.episodes.return_value = [1, 2]
        app.cli.confirm_incomplete_download.return_value = False
        app.cli.status.return_value = nullcontext()
        app._download_tools = Mock(return_value=("ffmpeg", "ffprobe"))
        app.settings = Mock()
        app.settings.get.return_value = "sequential"
        app.planner = Mock()
        app.planner.plan.return_value = SourcePlan(
            primary_player=None,
            routes={1: [], 2: []},
            verified_episodes=(1,),
            missing_episodes=(2,),
            players_used=("Player 1",),
        )

        app._download(catalogue())

        app.cli.confirm_incomplete_download.assert_called_once_with([1, 2], (2,))
        app.cli.warning.assert_called_once_with(
            "Verified 1/2 selected episodes across every player"
        )
        app.cli.info.assert_called_once_with(
            "Download cancelled before any transfer started"
        )
        manager.assert_not_called()


class LocalWatchTests(unittest.TestCase):
    @staticmethod
    def watch_application():
        app = Application.__new__(Application)
        app.cli = Mock()
        app.cli.status.return_value = nullcontext()
        app.settings = Mock()
        app.settings.get.side_effect = lambda key, default=None: {
            "watch_display": "window",
            "mpv_path": "mpv",
        }.get(key, default)
        app.settings.executable.return_value = "mpv"
        app.history = Mock()
        app.history.get.return_value = {
            "current_episode": 1,
            "position": 125,
        }
        app.planner = Mock()
        app.resolvers = Mock()
        app.probe = Mock()
        return app

    def test_verified_download_is_selected_as_local_media(self):
        app = Application.__new__(Application)
        app.cli = Mock()
        app.settings = Mock()
        app.settings.executable.return_value = "ffprobe"
        with TemporaryDirectory() as folder:
            app.settings.download_directory.return_value = Path(folder)
            episode = (
                Path(folder)
                / "Title"
                / "Season 1"
                / "EN"
                / "Episode 001.mp4"
            )
            episode.parent.mkdir(parents=True)
            episode.write_bytes(b"x" * 2048)
            with patch(
                "anistream.app.MediaValidator.validate",
                return_value=SimpleNamespace(valid=True, detail="MP4, h264"),
            ):
                media = app._local_episode_media(catalogue(), 1)

        self.assertIsNotNone(media)
        self.assertEqual(media.kind, "local")
        self.assertEqual(media.resolver_name, "Local file")
        app.cli.success.assert_called_once_with("Using verified local file for episode 1")

    def test_invalid_download_falls_back_to_online_sources(self):
        app = Application.__new__(Application)
        app.cli = Mock()
        app.settings = Mock()
        app.settings.executable.return_value = "ffprobe"
        with TemporaryDirectory() as folder:
            app.settings.download_directory.return_value = Path(folder)
            episode = (
                Path(folder)
                / "Title"
                / "Season 1"
                / "EN"
                / "Episode 001.mp4"
            )
            episode.parent.mkdir(parents=True)
            episode.write_bytes(b"x" * 2048)
            with patch(
                "anistream.app.MediaValidator.validate",
                return_value=SimpleNamespace(valid=False, detail="no video stream"),
            ):
                media = app._local_episode_media(catalogue(), 1)

        self.assertIsNone(media)
        self.assertIn("no online fallback is available", app.cli.warning.call_args.args[0])

    @patch("anistream.app.PlaybackService")
    def test_watch_skips_remote_planning_when_local_episode_is_valid(self, playback_service):
        app = self.watch_application()
        local_media = ResolvedMedia(
            "C:\\media\\Episode 001.mp4",
            "C:\\media\\Episode 001.mp4",
            "Local file",
            kind="local",
        )
        app._local_episode_media = Mock(return_value=local_media)
        playback_service.return_value.play.return_value = False

        app._watch(catalogue(), start_episode=1)

        app.planner.plan.assert_not_called()
        self.assertIs(
            playback_service.return_value.play.call_args.kwargs["preferred_media"],
            local_media,
        )

    @patch("anistream.app.PlaybackService")
    def test_all_remote_sources_failing_returns_cleanly_with_progress_safe(self, playback_service):
        app = self.watch_application()
        app._local_episode_media = Mock(return_value=None)
        app.planner.plan.return_value = SourcePlan(None, {1: []}, missing_episodes=(1,))
        playback_service.return_value.play.side_effect = ResolverError("all sources failed")

        app._watch(catalogue(), start_episode=1)

        self.assertIn("could not be played", app.cli.error.call_args.args[0])
        app.cli.info.assert_called_with(
            "Your watch progress is safe. You can retry this episode later."
        )

    @patch("anistream.app.PlaybackService")
    def test_missing_or_invalid_local_episode_uses_the_remote_plan(self, playback_service):
        app = self.watch_application()
        app._local_episode_media = Mock(return_value=None)
        remote_plan = SourcePlan("Player 1", {1: []})
        app.planner.plan.return_value = remote_plan
        playback_service.return_value.play.return_value = False

        app._watch(catalogue(), start_episode=1)

        app.planner.plan.assert_called_once_with(catalogue(), [1])
        self.assertIs(
            playback_service.return_value.play.call_args.args[2],
            remote_plan,
        )
        self.assertIsNone(
            playback_service.return_value.play.call_args.kwargs["preferred_media"]
        )

    @patch("anistream.app.PlaybackService")
    def test_local_sequence_offers_the_next_downloaded_episode_and_skips_gaps(self, playback_service):
        app = self.watch_application()
        app._local_episode_media = Mock(
            side_effect=lambda _catalogue, number: ResolvedMedia(
                f"C:\\media\\Episode {number:03d}.mp4",
                f"C:\\media\\Episode {number:03d}.mp4",
                "Local file",
                kind="local",
            )
        )
        playback_service.return_value.play.side_effect = [True, False]
        app.cli.confirm.return_value = True

        app._watch(
            catalogue(),
            start_episode=2,
            episode_sequence=(1, 2, 4),
        )

        played = [
            call.args[1].number
            for call in playback_service.return_value.play.call_args_list
        ]
        self.assertEqual(played, [2, 4])
        app.cli.confirm.assert_called_once_with("Play episode 4 now?", default=True)

    def test_continue_watching_uses_local_catalogue_when_provider_is_offline(self):
        entry = {
            "provider_id": "site",
            "provider_name": "Site",
            "catalogue_url": "https://site/title/season/en/",
            "title": "Title",
            "season": "Season 1",
            "language": "EN",
            "language_code": "en",
            "total_episodes": 12,
            "current_episode": 3,
            "status": "in_progress",
        }
        app = Application.__new__(Application)
        app.history = FakeHistory(entry)
        app.cli = FakeCli(entry)
        provider = FakeProvider(catalogue())
        provider.name = "Site"
        provider.catalogue = Mock(side_effect=ProviderError("site unavailable"))
        app.providers = FakeProviders(provider)
        offline = catalogue()
        app._offline_catalogue = Mock(return_value=offline)
        app._sync_history = Mock()
        app._watch = Mock()

        app._continue_watching()

        app._sync_history.assert_not_called()
        app._watch.assert_called_once_with(offline, start_episode=3)
        self.assertTrue(any("continuing from the local download" in message for message in app.cli.messages))

    def test_offline_catalogue_requires_the_current_downloaded_episode(self):
        entry = {
            "provider_id": "site",
            "provider_name": "Site",
            "catalogue_url": "https://site/title/season/en/",
            "title": "Title",
            "season": "Season 1",
            "language": "EN",
            "language_code": "en",
            "total_episodes": 12,
            "current_episode": 3,
        }
        app = Application.__new__(Application)
        app.settings = Mock()
        with TemporaryDirectory() as folder:
            app.settings.download_directory.return_value = Path(folder)
            self.assertIsNone(app._offline_catalogue(entry))
            episode = (
                Path(folder)
                / "Title"
                / "Season 1"
                / "EN"
                / "Episode 003.mp4"
            )
            episode.parent.mkdir(parents=True)
            episode.write_bytes(b"x" * 2048)
            offline = app._offline_catalogue(entry)

        self.assertIsNotNone(offline)
        self.assertEqual(len(offline.episodes), 12)
        self.assertEqual(offline.language.code, "en")

    @patch("anistream.app.LocalLibrary")
    def test_local_library_resumes_the_matching_downloaded_episode(self, library):
        history = {
            "provider_id": "site",
            "provider_name": "Site",
            "catalogue_url": "https://site/title/season/en/",
            "title": "Title",
            "season": "Season 1",
            "language": "EN",
            "language_code": "en",
            "total_episodes": 12,
            "current_episode": 2,
            "position": 90,
            "status": "in_progress",
        }
        entry = LocalLibraryEntry(
            "Title",
            "Season 1",
            "EN",
            Path("downloads/Title/Season 1/EN"),
            (1, 2, 4),
            history,
        )
        app = Application.__new__(Application)
        app.cli = Mock()
        app.cli.status.return_value = nullcontext()
        app.cli.choose_local_entry.return_value = entry
        app.settings = Mock()
        app.settings.download_directory.return_value = Path("downloads")
        app.history = Mock()
        app.history.all.return_value = [history]
        app.history.get.return_value = history
        app._watch = Mock()
        library.return_value.scan.return_value = [entry]

        app._local_library()

        library.assert_called_once_with(Path("downloads"))
        app._watch.assert_called_once_with(
            entry.catalogue(),
            start_episode=2,
            episode_sequence=(1, 2, 4),
        )
        app.cli.info.assert_called_once_with(
            "Resuming Title from downloaded episode 2..."
        )


if __name__ == "__main__":
    unittest.main()
