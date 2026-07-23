import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from anistream.models import Catalogue, Episode, MediaLanguage, ResolvedMedia
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


if __name__ == "__main__":
    unittest.main()
