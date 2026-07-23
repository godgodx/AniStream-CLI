import json
import unittest

from anistream.providers import default_providers
from anistream.providers.french_stream import FrenchStreamProvider


class Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeHttp:
    def __init__(self):
        self.user_agent = "test"

    def post(self, url, **kwargs):
        return Response(
            """
            <div class="search-item" onclick="location.href='/15120570-example-saison-2.html'">
              <div class="search-title">Example - Saison 2 (2025)</div>
            </div>
            <div class="search-item" onclick="location.href='/15127973-example-film.html'">
              <div class="search-title">Example Film (2026)</div>
            </div>
            <div class="search-item" onclick="location.href='https://lookalike.example/99-bad.html'">
              <div class="search-title">Bad result</div>
            </div>
            """
        )

    def get(self, url, **kwargs):
        if "get_seasons.php" in url:
            return Response(json.dumps([{"id": 15113307, "full_url": "15113307-example-saison-1.html"}]))
        if "static/series/15120570.js" in url:
            return Response(
                json.dumps(
                    {
                        "vf": {
                            "1": {"vidzy": "https://vidzy.org/embed-vf1.html", "uqload": "https://uqload.is/embed-vf1.html"},
                            "2": {"vidzy": "https://vidzy.org/embed-vf2.html", "uqload": "https://uqload.is/embed-vf2.html"},
                        },
                        "vostfr": {
                            "1": {"vidzy": "https://vidzy.org/embed-sub1.html", "uqload": "https://uqload.is/embed-sub1.html"},
                            "2": {"vidzy": "https://vidzy.org/embed-sub2.html", "uqload": "https://uqload.is/embed-sub2.html"},
                        },
                        "vo": {},
                        "info": {},
                    }
                )
            )
        if "static/series/15113307.js" in url:
            return Response(
                json.dumps(
                    {
                        "vf": {"1": {"vidzy": "https://vidzy.org/embed-s1.html", "uqload": "https://uqload.is/embed-s1.html"}},
                        "vostfr": {},
                        "vo": {},
                        "info": {},
                    }
                )
            )
        if "film_api.php" in url:
            return Response(
                json.dumps(
                    {
                        "players": {
                            "vidzy": {
                                "default": "https://vidzy.cc/embed-film-vf.html",
                                "vostfr": "https://vidzy.cc/embed-film-sub.html",
                                "vff": "https://vidzy.cc/embed-film-vff.html",
                            },
                            "uqload": {
                                "default": "https://uqload.is/embed-film-vf.html",
                                "vostfr": "https://uqload.is/embed-film-sub.html",
                                "vff": "https://uqload.is/embed-film-vff.html",
                            },
                        },
                        "meta": {},
                    }
                )
            )
        if "15120570-example-saison-2.html" in url:
            return Response(
                '<div id="serie-data" data-newsid="15120570" '
                'data-title="Example - Saison 2">s-100088</div>'
            )
        if "15113307-example-saison-1.html" in url:
            return Response(
                '<div id="serie-data" data-newsid="15113307" '
                'data-title="Example - Saison 1">s-100088</div>'
            )
        if "15127973-example-film.html" in url:
            return Response(
                '<div id="film-data" data-newsid="15127973" data-title="Example Film"></div>'
                '<a href="/index.php?do=xfsearch&amp;xfname=version-film&amp;xf=VF%2BVOSTFR">VF+VOSTFR</a>'
            )
        return Response("", 404)


class FrenchStreamProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = FrenchStreamProvider(FakeHttp())

    def test_detects_only_the_supported_hostname(self):
        self.assertTrue(self.provider.matches("https://french-stream.one/15127973-example.html"))
        self.assertTrue(self.provider.matches("french-stream.one/15127973-example.html"))
        self.assertFalse(self.provider.matches("https://french-stream.one.evil.example/15127973-example.html"))
        self.assertFalse(self.provider.matches("https://french-stream.example/15127973-example.html"))

    def test_search_uses_ajax_results_and_labels_the_provider(self):
        results = self.provider.search("example")
        self.assertEqual([item.title for item in results], ["Example - Saison 2", "Example Film"])
        self.assertTrue(all(item.provider_id == "french_stream" for item in results))
        self.assertTrue(all(item.provider_name == "French Stream" for item in results))

    def test_series_variants_include_related_seasons_and_languages(self):
        variants = self.provider.variants("https://french-stream.one/15120570-example-saison-2.html")
        self.assertEqual(
            [(item.season, item.language.code) for item in variants],
            [("Season 1", "vf"), ("Season 2", "vf"), ("Season 2", "vostfr")],
        )
        self.assertTrue(all("anistream_lang=" in item.url for item in variants))

    def test_unavailable_related_season_does_not_hide_current_variants(self):
        class PartiallyUnavailableHttp(FakeHttp):
            def get(self, url, **kwargs):
                if "15113307-example-saison-1.html" in url:
                    return Response("", 503)
                return super().get(url, **kwargs)

        provider = FrenchStreamProvider(PartiallyUnavailableHttp())
        variants = provider.variants("https://french-stream.one/15120570-example-saison-2.html")

        self.assertEqual(
            [(item.season, item.language.code) for item in variants],
            [("Season 2", "vf"), ("Season 2", "vostfr")],
        )

    def test_series_catalogue_preserves_language_and_player_order(self):
        url = "https://french-stream.one/15120570-example-saison-2.html?anistream_lang=vostfr"
        catalogue = self.provider.catalogue(url)
        self.assertEqual(catalogue.title, "Example")
        self.assertEqual(catalogue.season, "Season 2")
        self.assertEqual(catalogue.language.code, "vostfr")
        self.assertEqual(len(catalogue.episodes), 2)
        self.assertEqual([candidate.player for candidate in catalogue.episodes[0].candidates], ["Vidzy", "Uqload"])

    def test_film_variants_and_single_episode_catalogue(self):
        url = "https://french-stream.one/15127973-example-film.html"
        variants = self.provider.variants(url)
        self.assertEqual([item.language.code for item in variants], ["vf", "vostfr", "vff"])
        catalogue = self.provider.catalogue(next(item.url for item in variants if item.language.code == "vff"))
        self.assertEqual(catalogue.season, "Movie")
        self.assertEqual(catalogue.language.label, "TrueFrench (VFF)")
        self.assertEqual(len(catalogue.episodes), 1)
        self.assertEqual(len(catalogue.episodes[0].candidates), 2)

    def test_internal_language_deep_link_returns_only_that_variant(self):
        url = "https://french-stream.one/15120570-example-saison-2.html?anistream_lang=vf"
        variants = self.provider.variants(url)
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].language.code, "vf")

    def test_default_provider_factory_enables_french_stream(self):
        self.assertIn("french_stream", {provider.id for provider in default_providers(FakeHttp())})


if __name__ == "__main__":
    unittest.main()
