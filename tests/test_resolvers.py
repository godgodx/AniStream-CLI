import unittest

from anistream.resolvers.hosts import VidzyResolver


class Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeHttp:
    user_agent = "test-agent"

    def get(self, url, **kwargs):
        packed = (
            "eval(function(p,a,c,k,e,d){return p;}('0:\"1\"',2,2,"
            "'file|https://media.example/master.m3u8'.split('|'),0,{}))"
        )
        return Response(packed)


class VidzyResolverTests(unittest.TestCase):
    def test_matches_only_vidzy_hosts(self):
        resolver = VidzyResolver(FakeHttp())
        self.assertTrue(resolver.matches("https://vidzy.org/embed-id.html"))
        self.assertTrue(resolver.matches("https://cdn.vidzy.cc/embed-id.html"))
        self.assertFalse(resolver.matches("https://vidzy.org.evil.example/embed-id.html"))

    def test_resolves_packed_hls_and_supplies_media_headers(self):
        embed = "https://vidzy.org/embed-id.html"
        media = VidzyResolver(FakeHttp()).resolve(embed)
        self.assertEqual(media.url, "https://media.example/master.m3u8")
        self.assertEqual(media.kind, "hls")
        self.assertEqual(media.headers["Referer"], "https://vidzy.org/")
        self.assertEqual(media.headers["Origin"], "https://vidzy.org")


if __name__ == "__main__":
    unittest.main()
