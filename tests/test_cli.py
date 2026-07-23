import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from anistream.cli import Confirm, IntPrompt, Prompt, WORDMARK, Cli, format_watch_progress, parse_episode_selection
from anistream.models import CatalogueVariant, MediaLanguage


class EpisodeSelectionTests(unittest.TestCase):
    def test_all_and_latest(self):
        self.assertEqual(parse_episode_selection("all", 4), [1, 2, 3, 4])
        self.assertEqual(parse_episode_selection("latest", 4), [4])

    def test_lists_ranges_and_duplicates(self):
        self.assertEqual(parse_episode_selection("1, 3-5, 4", 6), [1, 3, 4, 5])
        self.assertEqual(parse_episode_selection("5-3", 6), [3, 4, 5])

    def test_out_of_range_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_episode_selection("0,2,9", 4)


class WatchProgressFormattingTests(unittest.TestCase):
    def test_in_progress_series_includes_episode_and_resume_time(self):
        item = {
            "status": "in_progress",
            "media_type": "series",
            "current_episode": 3,
            "total_episodes": 12,
            "position": 321,
        }
        self.assertEqual(format_watch_progress(item), "Episode 3/12 · 5:21")

    def test_completed_movie_and_series_have_clear_labels(self):
        movie = {"status": "completed", "media_type": "movie", "total_episodes": 1}
        series = {"status": "completed", "media_type": "series", "total_episodes": 12}
        self.assertEqual(format_watch_progress(movie), "Watched")
        self.assertEqual(format_watch_progress(series), "All 12 episodes")


class HeaderRenderingTests(unittest.TestCase):
    def render(self, width):
        cli = Cli.__new__(Cli)
        cli.console = Console(width=width, record=True, color_system=None)
        cli.header()
        return cli.console.export_text(styles=False)

    def test_wordmark_is_centered_and_fits_standard_terminal(self):
        output = self.render(80)
        self.assertIn("ANISTREAM CLI", output)
        self.assertIn("D I S C O V E R", output)
        self.assertIn("#####", output)
        self.assertEqual({len(line) for line in WORDMARK}, {61})
        self.assertLessEqual(max(map(len, output.splitlines())), 80)

    def test_compact_banner_is_used_in_narrow_terminal(self):
        output = self.render(50)
        self.assertIn("A N I S T R E A M", output)
        self.assertIn("DISCOVER  •  STREAM  •  DOWNLOAD", output)
        self.assertNotIn("#####", output)
        self.assertLessEqual(max(map(len, output.splitlines())), 50)

    @patch.dict("anistream.cli.os.environ", {"COLUMNS": "120", "LINES": "30"})
    def test_cli_does_not_freeze_terminal_dimensions_from_environment(self):
        cli = Cli(None, None)
        self.assertNotIn("COLUMNS", cli.console._environ)
        self.assertNotIn("LINES", cli.console._environ)
        self.assertIsNone(cli.console._width)
        self.assertIsNone(cli.console._height)


class MainMenuRenderingTests(unittest.TestCase):
    class History:
        @staticmethod
        def all():
            return []

    def test_menu_and_prompt_are_centered(self):
        cli = Cli.__new__(Cli)
        cli.history = self.History()
        cli.console = Console(width=100, record=True, color_system=None)
        with patch("anistream.cli.Prompt.ask", return_value="q") as ask:
            self.assertEqual(cli.main_choice(), "q")

        output = cli.console.export_text(styles=False)
        menu_line = next(line for line in output.splitlines() if "Continue Watching" in line)
        self.assertGreater(len(menu_line) - len(menu_line.lstrip()), 10)
        prompt = ask.call_args.args[0]
        self.assertGreater(len(prompt.plain) - len(prompt.plain.lstrip()), 10)


class ScreenNavigationTests(unittest.TestCase):
    def test_main_screen_clears_previous_content_before_header(self):
        cli = Cli.__new__(Cli)
        cli.console = Mock()
        with patch.object(cli, "header") as header:
            cli.main_screen()

        cli.console.clear.assert_called_once_with()
        header.assert_called_once_with()

    def test_input_screen_clears_and_centers_its_context(self):
        cli = Cli.__new__(Cli)
        cli.console = Console(width=100, record=True, color_system=None)
        cli.clear_screen = Mock()

        cli.input_screen("Search", "Search every enabled site by title.")

        cli.clear_screen.assert_called_once_with()
        output = cli.console.export_text(styles=False)
        line = next(line for line in output.splitlines() if "Search" in line)
        left = len(line) - len(line.lstrip())
        content = len(line.strip())
        right = 100 - left - content
        self.assertLessEqual(abs(left - right), 1)

    def test_secondary_menu_and_prompt_are_centered(self):
        cli = Cli.__new__(Cli)
        cli.console = Console(width=100, record=True, color_system=None)
        with patch("anistream.cli.IntPrompt.ask", return_value=0) as ask:
            self.assertIsNone(cli.choose_item("Available versions", ["value"], ["Season 1 - EN"]))

        output = cli.console.export_text(styles=False)
        option_line = next(line for line in output.splitlines() if "Season 1 - EN" in line)
        self.assertGreater(len(option_line) - len(option_line.lstrip()), 10)
        prompt = ask.call_args.args[0]
        self.assertGreater(len(prompt.plain) - len(prompt.plain.lstrip()), 10)

    def test_variant_menu_uses_structured_language_metadata(self):
        cli = Cli.__new__(Cli)
        cli.console = Console(width=100, record=True, color_system=None)
        variant = CatalogueVariant(
            "provider text that should not leak",
            "https://site/title/season/fr/",
            "Season 1",
            MediaLanguage("fr-dub", "French dub"),
        )
        with patch("anistream.cli.IntPrompt.ask", return_value=0):
            self.assertIsNone(cli.choose_variant([variant]))

        output = cli.console.export_text(styles=False)
        self.assertIn("Season 1 - French dub", output)
        self.assertNotIn("provider text that should not leak", output)


class LoadingRenderingTests(unittest.TestCase):
    def test_spinner_and_message_are_centered_as_one_unit(self):
        cli = Cli.__new__(Cli)
        cli.console = Console(width=100, record=True, color_system=None)
        loading = cli.status("Loading episodes...")
        cli.console.print(loading.renderable)

        output = cli.console.export_text(styles=False)
        line = next(line for line in output.splitlines() if "Loading episodes..." in line)
        left = len(line) - len(line.lstrip())
        content = len(line.strip())
        right = 100 - left - content
        self.assertLessEqual(abs(left - right), 1)


class NotificationRenderingTests(unittest.TestCase):
    def test_playback_messages_are_centered_as_one_unit(self):
        cli = Cli.__new__(Cli)
        cli.console = Console(width=100, record=True, color_system=None)

        cli.info("Streaming episode 2 with Sibnet...")

        output = cli.console.export_text(styles=False)
        line = next(line for line in output.splitlines() if "Streaming episode 2" in line)
        left = len(line) - len(line.lstrip())
        content = len(line.strip())
        right = 100 - left - content
        self.assertLessEqual(abs(left - right), 1)

    def test_notification_text_does_not_interpret_title_markup(self):
        cli = Cli.__new__(Cli)
        cli.console = Console(width=100, record=True, color_system=None)

        cli.info("Resuming Title [Special] at episode 2...")

        self.assertIn("Title [Special]", cli.console.export_text(styles=False))


class PromptValidationRenderingTests(unittest.TestCase):
    def test_every_prompt_type_adds_a_blank_line_before_input(self):
        cases = (
            (Prompt, lambda cli: cli.ask("Title"), "value"),
            (IntPrompt, lambda cli: cli.ask_int("Episode", default=1), 1),
            (Confirm, lambda cli: cli.confirm("Continue?", default=True), True),
        )
        for prompt_type, action, answer in cases:
            with self.subTest(prompt=prompt_type.__name__):
                cli = Cli.__new__(Cli)
                cli.console = Console(width=100, record=True, color_system=None)
                with patch.object(prompt_type, "ask", return_value=answer):
                    action(cli)

                self.assertEqual(cli.console.export_text(styles=False), "\n")

    def test_every_validation_error_is_centered(self):
        cases = (
            (Prompt, ["invalid", "1"], lambda cli: cli.ask("Choose", choices=["1", "2"], default="1"), "Please select"),
            (IntPrompt, ["invalid", "2"], lambda cli: cli.ask_int("Episode", default=1), "valid integer"),
            (Confirm, ["invalid", "y"], lambda cli: cli.confirm("Continue?", default=True), "Please enter Y or N"),
        )
        for prompt_type, answers, action, expected in cases:
            with self.subTest(prompt=prompt_type.__name__):
                cli = Cli.__new__(Cli)
                cli.console = Console(width=100, record=True, color_system=None)
                with patch.object(prompt_type, "get_input", side_effect=answers):
                    action(cli)

                output = cli.console.export_text(styles=False)
                line = next(line for line in output.splitlines() if expected in line)
                left = len(line) - len(line.lstrip())
                content = len(line.strip())
                right = 100 - left - content
                self.assertLessEqual(abs(left - right), 1)


if __name__ == "__main__":
    unittest.main()
