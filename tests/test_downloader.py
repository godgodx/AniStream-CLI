import tempfile
import unittest
from pathlib import Path

from anistream.models import Catalogue, EmbedCandidate, Episode, ProbeResult, ResolvedMedia
from anistream.services.downloader import DownloadManager
from anistream.services.media_validator import ValidationResult
from anistream.services.source_planner import SourcePlan


class FakeRegistry:
    def resolve(self, embed_url):
        return ResolvedMedia(embed_url.replace("embed", "media"), embed_url, "Fake")


class FakeProbe:
    def probe(self, media):
        return ProbeResult(True, "mp4", "ok")


class FakeValidator:
    def validate(self, path):
        return ValidationResult(path.exists() and path.stat().st_size > 0, "verified MP4")


class FakeDownloadManager(DownloadManager):
    def _run_ffmpeg(self, media_url, headers, output):
        if "bad" in media_url:
            return "simulated media failure"
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
            "EN",
            (Episode(1, (EmbedCandidate("Player 1", "https://embed/bad"), EmbedCandidate("Player 2", "https://embed/good"))),),
        )
        plan = SourcePlan(None, {1: list(catalogue.episodes[0].candidates)})
        with tempfile.TemporaryDirectory() as folder:
            manager = FakeDownloadManager(
                ffmpeg_path="ffmpeg",
                validator=FakeValidator(),
                resolvers=FakeRegistry(),
                probe=FakeProbe(),
                download_root=Path(folder),
            )
            result = manager.download(catalogue, [1], plan, parallel=False)[0]
        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 2)
        self.assertFalse(result.attempts[0].success)
        self.assertTrue(result.attempts[1].success)


if __name__ == "__main__":
    unittest.main()
