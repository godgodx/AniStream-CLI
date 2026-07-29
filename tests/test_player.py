import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from anistream.errors import PlaybackError
from anistream.models import (
    Catalogue,
    EmbedCandidate,
    Episode,
    MediaLanguage,
    ProbeResult,
    ResolvedMedia,
)
from anistream.services.player import PlaybackService


class PlayerOutputTests(unittest.TestCase):
    def test_mpv_native_terminal_output_is_quiet_in_window_mode(self):
        history = Mock()
        history.get.return_value = None
        service = PlaybackService(
            mpv_path="mpv",
            display_mode="window",
            history=history,
            resolvers=Mock(),
            probe=Mock(),
        )
        episode = Episode(1, ())
        catalogue = Catalogue(
            "site",
            "Site",
            "Title",
            "https://site/title/season/en/",
            "Season 1",
            MediaLanguage("en", "EN"),
            (episode,),
        )
        media = ResolvedMedia(
            url="https://media.example/video.mp4",
            embed_url="https://embed.example/video",
            resolver_name="Example",
        )
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0

        with TemporaryDirectory() as temporary_directory:
            with (
                patch.object(service, "_pick_media", return_value=media),
                patch.object(service, "_watch_later_dir", return_value=Path(temporary_directory)),
                patch("anistream.services.player.subprocess.Popen", return_value=process) as popen,
                patch("anistream.services.player._WindowsKillOnCloseJob") as job,
            ):
                job.return_value.active = True
                service.play(catalogue, episode)

        command = popen.call_args.args[0]
        self.assertIn("--really-quiet", command)
        self.assertEqual(command.count("--really-quiet"), 1)
        for option in (
            "--no-config",
            "--load-scripts=no",
            "--ytdl=no",
            "--load-unsafe-playlists=no",
            "--no-use-filedir-conf",
        ):
            self.assertIn(option, command)
            self.assertLess(command.index(option), command.index(media.url))
        self.assertIs(popen.call_args.kwargs.get("shell"), False)

    def test_mpv_failure_preserves_resume_position_and_raises_clean_error(self):
        history = Mock()
        history.get.return_value = {"current_episode": 1, "position": 321}
        service = PlaybackService(
            mpv_path="mpv",
            display_mode="window",
            history=history,
            resolvers=Mock(),
            probe=Mock(),
        )
        episode = Episode(1, ())
        catalogue = Catalogue(
            "site",
            "Site",
            "Title",
            "https://site/title/season/en/",
            "Season 1",
            MediaLanguage("en", "EN"),
            (episode,),
        )
        media = ResolvedMedia(
            url="https://media.example/video.mp4",
            embed_url="https://embed.example/video",
            resolver_name="Example",
        )
        process = Mock()
        process.wait.return_value = 2
        process.poll.return_value = 2

        with TemporaryDirectory() as temporary_directory:
            with (
                patch.object(service, "_pick_media", return_value=media),
                patch.object(service, "_watch_later_dir", return_value=Path(temporary_directory)),
                patch("anistream.services.player.subprocess.Popen", return_value=process),
                patch("anistream.services.player._WindowsKillOnCloseJob") as job,
            ):
                job.return_value.active = True
                with self.assertRaisesRegex(PlaybackError, "all playback sources failed"):
                    service.play(catalogue, episode)

        self.assertEqual(history.update.call_args.kwargs["position"], 321)
        self.assertFalse(history.update.call_args.kwargs["completed"])

    def test_preferred_local_media_keeps_the_same_watch_later_identity(self):
        history = Mock()
        history.get.return_value = {"current_episode": 1, "position": 42}
        service = PlaybackService(
            mpv_path="mpv",
            display_mode="window",
            history=history,
            resolvers=Mock(),
            probe=Mock(),
        )
        episode = Episode(1, ())
        catalogue = Catalogue(
            "site",
            "Site",
            "Title",
            "https://site/title/season/en/",
            "Season 1",
            MediaLanguage("en", "EN"),
            (episode,),
        )
        local = ResolvedMedia(
            url="C:\\downloads\\Title\\Episode 001.mp4",
            embed_url="C:\\downloads\\Title\\Episode 001.mp4",
            resolver_name="Local file",
            kind="local",
        )
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0

        with TemporaryDirectory() as temporary_directory:
            watch_dir = Path(temporary_directory)
            (watch_dir / "state-from-previous-remote-source").write_text(
                "start=42.0\n",
                encoding="utf-8",
            )
            with (
                patch.object(service, "_pick_media") as pick_media,
                patch.object(service, "_watch_later_dir", return_value=watch_dir),
                patch("anistream.services.player.subprocess.Popen", return_value=process) as popen,
                patch("anistream.services.player._WindowsKillOnCloseJob") as job,
            ):
                job.return_value.active = True
                service.play(catalogue, episode, preferred_media=local)

        pick_media.assert_not_called()
        command = popen.call_args.args[0]
        self.assertIn(str(local.url), command)
        self.assertIn(f"--watch-later-dir={watch_dir}", command)
        self.assertIn("--start=42.000", command)
        self.assertTrue(history.update.call_args.kwargs["completed"])

    def test_local_quit_tracks_the_new_position_without_reading_stale_remote_state(self):
        history = Mock()
        history.get.return_value = {"current_episode": 1, "position": 42}
        service = PlaybackService(
            mpv_path="mpv",
            display_mode="window",
            history=history,
            resolvers=Mock(),
            probe=Mock(),
        )
        episode = Episode(1, ())
        catalogue = Catalogue(
            "site",
            "Site",
            "Title",
            "https://site/title/season/en/",
            "Season 1",
            MediaLanguage("en", "EN"),
            (episode,),
        )
        local = ResolvedMedia(
            url="C:\\downloads\\Title\\Episode 001.mp4",
            embed_url="C:\\downloads\\Title\\Episode 001.mp4",
            resolver_name="Local file",
            kind="local",
        )

        with TemporaryDirectory() as temporary_directory:
            watch_dir = Path(temporary_directory)
            (watch_dir / "old-remote-state").write_text("start=42.0\n", encoding="utf-8")
            process = Mock()

            def save_and_exit():
                (watch_dir / "new-local-state").write_text("start=125.5\n", encoding="utf-8")
                return 0

            process.wait.side_effect = save_and_exit
            process.poll.return_value = 0
            with (
                patch.object(service, "_watch_later_dir", return_value=watch_dir),
                patch("anistream.services.player.subprocess.Popen", return_value=process),
                patch("anistream.services.player._WindowsKillOnCloseJob") as job,
            ):
                job.return_value.active = True
                finished = service.play(catalogue, episode, preferred_media=local)

        self.assertFalse(finished)
        self.assertEqual(history.update.call_args.kwargs["position"], 125.5)
        self.assertFalse(history.update.call_args.kwargs["completed"])

    def test_mpv_failure_switches_source_and_resumes_the_saved_position(self):
        history = Mock()
        history.get.return_value = {"current_episode": 1, "position": 42}
        candidates = (
            EmbedCandidate("Player 1", "https://embed.example/one"),
            EmbedCandidate("Player 2", "https://embed.example/two"),
        )
        episode = Episode(1, candidates)
        catalogue = Catalogue(
            "site",
            "Site",
            "Title",
            "https://site/title/season/en/",
            "Season 1",
            MediaLanguage("en", "EN"),
            (episode,),
        )
        media = [
            ResolvedMedia(
                "https://media.example/one.m3u8",
                candidates[0].url,
                "First",
                kind="hls",
            ),
            ResolvedMedia(
                "https://media.example/two.m3u8",
                candidates[1].url,
                "Second",
                kind="hls",
            ),
        ]
        resolvers = Mock()
        resolvers.resolve.side_effect = media
        probe = Mock()
        probe.probe.return_value = ProbeResult(True, "hls", "ok")
        service = PlaybackService(
            mpv_path="mpv",
            display_mode="window",
            history=history,
            resolvers=resolvers,
            probe=probe,
        )

        with TemporaryDirectory() as temporary_directory:
            watch_dir = Path(temporary_directory)
            first = Mock()

            def fail_after_progress():
                (watch_dir / "first-source").write_text(
                    "start=125.5\n",
                    encoding="utf-8",
                )
                return 2

            first.wait.side_effect = fail_after_progress
            first.poll.return_value = 2
            second = Mock()
            second.wait.return_value = 0
            second.poll.return_value = 0
            with (
                patch.object(service, "_watch_later_dir", return_value=watch_dir),
                patch(
                    "anistream.services.player.subprocess.Popen",
                    side_effect=[first, second],
                ) as popen,
                patch("anistream.services.player._WindowsKillOnCloseJob") as job,
            ):
                job.return_value.active = True
                finished = service.play(catalogue, episode)

        self.assertTrue(finished)
        self.assertEqual(popen.call_count, 2)
        second_command = popen.call_args_list[1].args[0]
        self.assertIn("--start=125.500", second_command)
        self.assertEqual(history.update.call_args_list[0].kwargs["position"], 125.5)
        self.assertFalse(history.update.call_args_list[0].kwargs["completed"])
        self.assertTrue(history.update.call_args_list[1].kwargs["completed"])


if __name__ == "__main__":
    unittest.main()
