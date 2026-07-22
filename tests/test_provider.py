import unittest

from anistream.providers.anime_sama import AnimeSamaProvider
from anistream.providers.registry import ProviderRegistry


class Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeHttp:
    def __init__(self):
        self.user_agent = "test"

    def post(self, url, **kwargs):
        return Response(
            '<a href="https://anime-sama.to/catalogue/example-title">'
            '<h3>Example Title</h3></a>'
        )

    def get(self, url, **kwargs):
        if url.endswith("episodes.js"):
            response = Response(
                "var eps1 = ['https://sendvid.com/a', 'https://sendvid.com/b'];\n"
                'var eps2 = ["https://video.sibnet.ru/shell.php?videoid=1", '
                '"https://video.sibnet.ru/shell.php?videoid=2"];'
            )
            if "/saison1/vostfr/" in url or "/saison2/vf/" in url:
                return response
            return Response("", 404)
        return Response(
            'panneauAnime("nom", "url");\n'
            'panneauAnime("Season 1", "saison1/vostfr");\n'
            'panneauAnime("Season 2", "saison2/vf");'
        )


class AnimeSamaProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = AnimeSamaProvider(FakeHttp())

    def test_detects_any_anime_sama_domain(self):
        self.assertTrue(self.provider.matches("https://anime-sama.to/catalogue/test/"))
        self.assertTrue(self.provider.matches("https://www.anime-sama.example/catalogue/test/"))
        self.assertFalse(self.provider.matches("https://example.com/catalogue/test/"))

    def test_search_labels_provider(self):
        result = self.provider.search("example")[0]
        self.assertEqual(result.provider_name, "Anime-Sama")
        self.assertEqual(result.title, "Example Title")

    def test_registry_finds_provider_by_stable_id(self):
        registry = ProviderRegistry([self.provider])
        self.assertIs(registry.get("anime_sama"), self.provider)
        self.assertIsNone(registry.get("missing"))

    def test_variants_and_episode_matrix(self):
        variants = self.provider.variants("https://anime-sama.to/catalogue/example-title/")
        self.assertEqual(len(variants), 2)
        catalogue = self.provider.catalogue(variants[0].url)
        self.assertEqual(catalogue.title, "Example Title")
        self.assertEqual(len(catalogue.episodes), 2)
        self.assertEqual([item.player for item in catalogue.episodes[0].candidates], ["Player 1", "Player 2"])


if __name__ == "__main__":
    unittest.main()
