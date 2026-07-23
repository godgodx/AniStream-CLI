import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anistream.services.media_validator import MediaValidator


class MediaValidatorTests(unittest.TestCase):
    def validate_payload(self, payload):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "Episode 001.mp4"
            media.write_bytes(b"x" * 2048)
            completed = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            with patch("anistream.services.media_validator.subprocess.run", return_value=completed):
                return MediaValidator("ffprobe").validate(media)

    def test_rejects_image_disguised_as_mp4(self):
        result = self.validate_payload(
            {
                "format": {"format_name": "mov,mp4", "duration": "5.8"},
                "streams": [{"codec_type": "video", "codec_name": "png", "width": 1, "height": 1}],
            }
        )
        self.assertFalse(result.valid)
        self.assertIn("invalid video stream", result.detail)

    def test_accepts_plausible_mp4_video(self):
        result = self.validate_payload(
            {
                "format": {"format_name": "mov,mp4", "duration": "1440"},
                "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}],
            }
        )
        self.assertTrue(result.valid)
        self.assertIn("h264", result.detail)

    def test_remote_duration_uses_the_longest_reported_duration(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"duration": "1439.5"},
                    "streams": [{"duration": "1440.0"}, {"duration": "1438.0"}],
                }
            ),
            stderr="",
        )
        with patch("anistream.services.media_validator.subprocess.run", return_value=completed) as run:
            duration = MediaValidator("ffprobe").probe_duration(
                "https://media.example/episode.m3u8",
                {"Referer": "https://embed.example/"},
            )

        self.assertEqual(duration, 1440.0)
        command = run.call_args.args[0]
        self.assertIn("-headers", command)
        self.assertEqual(command[-1], "https://media.example/episode.m3u8")
        self.assertEqual(run.call_args.kwargs["timeout"], 15)


if __name__ == "__main__":
    unittest.main()
