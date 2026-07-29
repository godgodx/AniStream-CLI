import unittest
import socket
from unittest.mock import patch

import requests
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
        client.get(
            "https://anime-sama.to/catalogue/title/",
            allow_redirects=False,
            stream=True,
        )
        client.get(
            "https://video.example/embed/123",
            allow_redirects=False,
            stream=True,
        )
        first_headers = session.calls[0][2]["headers"]
        second_headers = session.calls[1][2]["headers"]
        self.assertEqual(first_headers.get("Cookie"), "test-cookie-value")
        self.assertNotIn("Cookie", second_headers)

    def test_environment_proxies_are_disabled(self):
        self.assertFalse(HttpClient()._session().trust_env)

    def test_private_dns_answer_is_rejected(self):
        answer = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 443),
            )
        ]
        with (
            patch("anistream.utils.http.socket.getaddrinfo", return_value=answer),
            self.assertRaisesRegex(requests.exceptions.InvalidURL, "non-public"),
        ):
            HttpClient.validate_public_url("https://private.example/video")

    def test_request_path_rejects_private_dns_before_connecting(self):
        answer = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 443),
            )
        ]
        with (
            patch("anistream.utils.http.socket.getaddrinfo", return_value=answer),
            self.assertRaisesRegex(requests.exceptions.InvalidURL, "non-public"),
        ):
            HttpClient(retry_total=0).get(
                "https://private.example/video",
                allow_redirects=False,
                stream=True,
            )

    def test_credentials_and_nonstandard_ports_are_rejected(self):
        for url in (
            "https://user:password@example.com/video",
            "https://example.com:8443/video",
        ):
            with self.subTest(url=url):
                with self.assertRaises(requests.exceptions.InvalidURL):
                    HttpClient.validate_public_url(url)

    def test_non_streaming_response_is_bounded(self):
        response = requests.Response()
        response.status_code = 200
        response.url = "https://example.com/data"
        response.headers = {}
        response._content_consumed = False
        response.iter_content = lambda _size: iter(
            (b"a" * (64 * 1024), b"b")
        )
        response.close = lambda: None
        client = HttpClient(max_response_bytes=64 * 1024)

        with self.assertRaisesRegex(
            requests.exceptions.RequestException,
            "size limit",
        ):
            client._bounded_response(response)

    def test_cross_host_redirect_strips_sensitive_headers(self):
        class RedirectResponse:
            def __init__(self, status, url, headers=None):
                self.status_code = status
                self.url = url
                self.headers = headers or {}

            def close(self):
                return None

        class RedirectSession:
            def __init__(self):
                self.calls = []
                self.responses = [
                    RedirectResponse(
                        302,
                        "https://provider.example/start",
                        {"Location": "https://cdn.example/video"},
                    ),
                    RedirectResponse(
                        200,
                        "https://cdn.example/video",
                    ),
                ]

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return self.responses.pop(0)

        client = HttpClient()
        session = RedirectSession()
        client._local.session = session
        client.get(
            "https://provider.example/start",
            headers={
                "Authorization": "secret",
                "cookie": "private=value",
            },
            stream=True,
        )

        redirected_headers = session.calls[1][2]["headers"]
        self.assertNotIn("Authorization", redirected_headers)
        self.assertNotIn("cookie", redirected_headers)


if __name__ == "__main__":
    unittest.main()
