import unittest

from anistream.models import ResolvedMedia
from anistream.services.media_probe import RemoteMediaProbe


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        url: str,
        *,
        status: int = 200,
        content_type: str = "application/vnd.apple.mpegurl",
    ):
        self.body = body
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def iter_content(self, _chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class RemoteMediaProbeTests(unittest.TestCase):
    def test_rejects_incomplete_vod_playlist(self):
        response = FakeResponse(
            b"#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n"
            b"#EXTINF:10,\nsegment.ts\n",
            "https://cdn.example/media.m3u8",
        )
        probe = RemoteMediaProbe(FakeHttp([response]))

        result = probe.probe(
            ResolvedMedia(
                "https://cdn.example/media.m3u8",
                "https://embed.example/video",
                "Example",
                kind="hls",
            )
        )

        self.assertFalse(result.valid)
        self.assertIn("incomplete", result.detail)
        self.assertTrue(response.closed)

    def test_master_checks_variant_key_and_first_segment(self):
        master = FakeResponse(
            b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nquality.m3u8\n",
            "https://cdn.example/master.m3u8",
        )
        variant = FakeResponse(
            b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
            b"#EXTINF:10,\nsegment.ts\n#EXT-X-ENDLIST\n",
            "https://cdn.example/quality.m3u8",
        )
        key = FakeResponse(
            b"0123456789abcdef",
            "https://cdn.example/key.bin",
            content_type="application/octet-stream",
        )
        segment = FakeResponse(
            b"media-bytes",
            "https://cdn.example/segment.ts",
            content_type="video/mp2t",
        )
        http = FakeHttp([master, variant, key, segment])

        result = RemoteMediaProbe(http).probe(
            ResolvedMedia(
                "https://cdn.example/master.m3u8",
                "https://embed.example/video",
                "Example",
                kind="hls",
            )
        )

        self.assertTrue(result.valid)
        self.assertEqual(
            [call[0] for call in http.calls],
            [
                "https://cdn.example/master.m3u8",
                "https://cdn.example/quality.m3u8",
                "https://cdn.example/key.bin",
                "https://cdn.example/segment.ts",
            ],
        )

    def test_empty_first_segment_rejects_otherwise_valid_playlist(self):
        playlist = FakeResponse(
            b"#EXTM3U\n#EXTINF:10,\nsegment.ts\n#EXT-X-ENDLIST\n",
            "https://cdn.example/media.m3u8",
        )
        segment = FakeResponse(
            b"",
            "https://cdn.example/segment.ts",
            content_type="video/mp2t",
        )

        result = RemoteMediaProbe(FakeHttp([playlist, segment])).probe(
            ResolvedMedia(
                "https://cdn.example/media.m3u8",
                "https://embed.example/video",
                "Example",
                kind="hls",
            )
        )

        self.assertFalse(result.valid)
        self.assertIn("empty", result.detail)

    def test_mp4_header_remains_supported(self):
        body = b"\x00\x00\x00\x18ftyp" + b"\x00" * 4096
        response = FakeResponse(
            body,
            "https://cdn.example/video.mp4",
            content_type="video/mp4",
        )

        result = RemoteMediaProbe(FakeHttp([response])).probe(
            ResolvedMedia(
                "https://cdn.example/video.mp4",
                "https://embed.example/video",
                "Example",
                kind="mp4",
            )
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.kind, "mp4")


if __name__ == "__main__":
    unittest.main()
