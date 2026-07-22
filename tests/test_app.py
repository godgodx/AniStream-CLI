import unittest
from contextlib import nullcontext
from unittest.mock import Mock

from anistream.app import Application
from anistream.models import Catalogue, Episode


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
        "EN",
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
            self.choices = iter(["4", "q"])
            self.main_screen_calls = 0
            self.settings_calls = 0

        def main_screen(self):
            self.main_screen_calls += 1

        def main_choice(self):
            return next(self.choices)

        def settings_menu(self):
            self.settings_calls += 1

    def test_header_is_redrawn_when_returning_to_main_menu(self):
        app = Application.__new__(Application)
        app.cli = self.Cli()

        self.assertEqual(app.run(), 0)
        self.assertEqual(app.cli.settings_calls, 1)
        self.assertEqual(app.cli.main_screen_calls, 2)


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

        self.assertIsNone(app._from_link())

        app.cli.input_screen.assert_called_once_with(
            "Open a link",
            "Paste a catalogue URL from any enabled site.",
        )


if __name__ == "__main__":
    unittest.main()
