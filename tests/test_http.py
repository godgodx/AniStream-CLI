import unittest

from anistream.utils.http import HttpClient


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return object()


class CookieIsolationTests(unittest.TestCase):
    def test_provider_cookie_is_never_sent_to_video_hosts(self):
        client = HttpClient(cookie="test-cookie-value", cookie_hosts={"anime-sama.to"})
        session = FakeSession()
        client._local.session = session
        client.get("https://anime-sama.to/catalogue/title/")
        client.get("https://video.example/embed/123")
        first_headers = session.calls[0][2]["headers"]
        second_headers = session.calls[1][2]["headers"]
        self.assertEqual(first_headers.get("Cookie"), "test-cookie-value")
        self.assertNotIn("Cookie", second_headers)


if __name__ == "__main__":
    unittest.main()
