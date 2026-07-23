import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from anistream.models import Catalogue, EmbedCandidate, Episode, MediaLanguage, ProbeResult, ResolvedMedia
from anistream.services.downloader import DownloadManager, DownloadProgress
from anistream.services.media_validator import ValidationResult
from anistream.services.source_planner import SourcePlan


class FakeRegistry:
    def resolve(self, embed_url):
        return ResolvedMedia(embed_url.replace("embed", "media"), embed_url, "Fake")


class FakeProbe:
    def probe(self, media):
        return ProbeResult(True, "mp4", "ok")


class FakeValidator:
    def probe_duration(self, _url, _headers):
        return 100.0

    def validate(self, path):
        return ValidationResult(path.exists() and path.stat().st_size > 0, "verified MP4")


class FakeDownloadManager(DownloadManager):
    def _run_ffmpeg(self, media_url, headers, output, transfer=None):
        if "bad" in media_url:
            return "simulated media failure"
        if transfer:
            transfer(50.0, 5 * 1024 * 1024, 2 * 1024 * 1024, 25.0)
        output.write_bytes(b"valid-media")
        return None


class DownloaderFallbackTests(unittest.TestCase):
    def test_failed_source_automatically_uses_next_embed(self):
        catalogue = Catalogue(
            "site",
            "Site",
            "Title",
            "https://site/title/season/en/",
            "Season 1",
            MediaLanguage("en", "EN"),
            (Episode(1, (EmbedCandidate("Player 1", "https://embed/bad"), EmbedCandidate("Player 2", "https://embed/good"))),),
        )
        plan = SourcePlan(None, {1: list(catalogue.episodes[0].candidates)})
        updates: list[DownloadProgress] = []
        with tempfile.TemporaryDirectory() as folder:
            manager = FakeDownloadManager(
                ffmpeg_path="ffmpeg",
                validator=FakeValidator(),
                resolvers=FakeRegistry(),
                probe=FakeProbe(),
                download_root=Path(folder),
            )
            result = manager.download(catalogue, [1], plan, parallel=False, progress=updates.append)[0]
        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 2)
        self.assertFalse(result.attempts[0].success)
        self.assertTrue(result.attempts[1].success)
        self.assertIn("retrying", [update.state for update in updates])
        transfer = next(update for update in updates if update.state == "downloading")
        self.assertEqual(transfer.percent, 50.0)
        self.assertEqual(transfer.bytes_per_second, 2 * 1024 * 1024)
        self.assertEqual(updates[-1].state, "completed")

    def test_ffmpeg_progress_time_parsing_accepts_machine_and_clock_formats(self):
        self.assertEqual(DownloadManager._progress_seconds({"out_time_us": "12500000"}), 12.5)
        self.assertEqual(DownloadManager._progress_seconds({"out_time": "01:02:03.500000"}), 3723.5)

    def test_ffmpeg_failure_keeps_diagnostics_that_contain_equals_signs(self):
        process = Mock()
        process.stdout = iter(["https://media.example/video?token=value: HTTP error 403\n"])
        process.wait.return_value = 1
        with tempfile.TemporaryDirectory() as folder:
            manager = DownloadManager(
                ffmpeg_path="ffmpeg",
                validator=FakeValidator(),
                resolvers=FakeRegistry(),
                probe=FakeProbe(),
                download_root=Path(folder),
            )
            with patch("anistream.services.downloader.subprocess.Popen", return_value=process) as popen:
                error = manager._run_ffmpeg(
                    "https://media.example/video?token=value",
                    {},
                    Path(folder) / "output.mp4",
                )

        self.assertEqual(error, "https://media.example/video?token=value: HTTP error 403")
        command = popen.call_args.args[0]
        self.assertIn("-progress", command)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.STDOUT)


if __name__ == "__main__":
    unittest.main()
