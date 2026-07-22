import unittest

from anistream.models import Catalogue, EmbedCandidate, Episode, ProbeResult, ResolvedMedia
from anistream.services.source_planner import SourcePlanner


class FakeResolver:
    name = "Fake"

    def resolve(self, url):
        return ResolvedMedia(url.replace("embed", "media") + ".mp4", url, self.name)


class FakeRegistry:
    def __init__(self):
        self.resolver = FakeResolver()

    def resolver_for(self, url):
        return self.resolver

    def supports(self, url):
        return True


class FakeProbe:
    def probe(self, media):
        if "broken" in media.url:
            return ProbeResult(False, detail="broken source")
        return ProbeResult(True, "mp4", "ok")


def catalogue():
    return Catalogue(
        "site",
        "Site",
        "Title",
        "https://site/title/season/en/",
        "Season 1",
        "EN",
        (
            Episode(1, (EmbedCandidate("Player 1", "https://embed/good-1"), EmbedCandidate("Player 2", "https://embed/good-3"))),
            Episode(2, (EmbedCandidate("Player 1", "https://embed/broken-2"), EmbedCandidate("Player 2", "https://embed/good-4"))),
        ),
    )


class SourcePlannerTests(unittest.TestCase):
    def test_selects_first_player_with_one_hundred_percent(self):
        planner = SourcePlanner(FakeRegistry(), FakeProbe())
        plan = planner.plan(catalogue(), [1, 2])
        self.assertEqual(plan.primary_player, "Player 2")
        self.assertEqual(plan.routes[1][0].player, "Player 2")
        self.assertEqual(plan.routes[2][0].player, "Player 2")

    def test_keeps_verified_per_episode_fallbacks(self):
        data = catalogue()
        altered = Catalogue(
            data.provider_id,
            data.provider_name,
            data.title,
            data.url,
            data.season,
            data.language,
            (
                data.episodes[0],
                Episode(2, (EmbedCandidate("Player 1", "https://embed/broken-a"), EmbedCandidate("Player 2", "https://embed/broken-b"))),
            ),
        )
        plan = SourcePlanner(FakeRegistry(), FakeProbe()).plan(altered, [1, 2])
        self.assertIsNone(plan.primary_player)
        self.assertIn((1, "https://embed/good-1"), plan.cache)


if __name__ == "__main__":
    unittest.main()
