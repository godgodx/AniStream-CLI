import unittest
import time
from unittest.mock import patch

from anistream.models import Catalogue, EmbedCandidate, Episode, MediaLanguage, ProbeResult, ResolvedMedia
from anistream.services.source_planner import SourcePlanner
from anistream.services.source_health import SourceHealthTracker


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
        MediaLanguage("en", "EN"),
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
        self.assertEqual(plan.verified_episodes, (1,))
        self.assertEqual(plan.missing_episodes, (2,))
        self.assertFalse(plan.complete)

    def test_rebuilds_complete_coverage_from_partial_players(self):
        data = catalogue()
        partial = Catalogue(
            data.provider_id,
            data.provider_name,
            data.title,
            data.url,
            data.season,
            data.language,
            (
                Episode(1, (EmbedCandidate("Player 1", "https://embed/good-1"),)),
                Episode(2, (EmbedCandidate("Player 2", "https://embed/good-2"),)),
            ),
        )

        plan = SourcePlanner(FakeRegistry(), FakeProbe()).plan(partial, [1, 2])

        self.assertIsNone(plan.primary_player)
        self.assertTrue(plan.complete)
        self.assertEqual(plan.verified_episodes, (1, 2))
        self.assertEqual(plan.missing_episodes, ())
        self.assertEqual(plan.players_used, ("Player 1", "Player 2"))
        self.assertEqual(plan.routes[1][0].player, "Player 1")
        self.assertEqual(plan.routes[2][0].player, "Player 2")

    def test_rebuilds_complete_coverage_from_complementary_working_links(self):
        data = catalogue()
        complementary = Catalogue(
            data.provider_id,
            data.provider_name,
            data.title,
            data.url,
            data.season,
            data.language,
            (
                Episode(
                    1,
                    (
                        EmbedCandidate("Player 1", "https://embed/good-1"),
                        EmbedCandidate("Player 2", "https://embed/broken-1"),
                    ),
                ),
                Episode(
                    2,
                    (
                        EmbedCandidate("Player 1", "https://embed/broken-2"),
                        EmbedCandidate("Player 2", "https://embed/good-2"),
                    ),
                ),
            ),
        )

        plan = SourcePlanner(FakeRegistry(), FakeProbe()).plan(complementary, [1, 2])

        self.assertTrue(plan.complete)
        self.assertEqual(plan.players_used, ("Player 1", "Player 2"))
        self.assertEqual(plan.routes[1][0].player, "Player 1")
        self.assertEqual(plan.routes[2][0].player, "Player 2")

    def test_recent_failure_reorders_sources_without_hardcoding_a_host(self):
        health = SourceHealthTracker()
        data = catalogue()
        first_urls = [
            episode.candidates[0].url
            for episode in data.episodes
        ]
        for url in first_urls:
            health.observe(url, latency_seconds=8.0, success=False)

        plan = SourcePlanner(
            FakeRegistry(),
            FakeProbe(),
            source_health=health,
        ).plan(data, [1, 2])

        self.assertEqual(plan.primary_player, "Player 2")
        self.assertEqual(plan.routes[1][0].player, "Player 2")

    def test_player_preflight_has_a_bounded_deadline(self):
        data = Catalogue(
            "site",
            "Site",
            "Title",
            "https://site/title",
            "Season 1",
            MediaLanguage("en", "EN"),
            (
                Episode(
                    1,
                    (EmbedCandidate("Slow", "https://embed/slow"),),
                ),
            ),
        )
        registry = FakeRegistry()

        def stall(_url):
            time.sleep(0.1)
            return ResolvedMedia(
                "https://media/slow.mp4",
                "https://embed/slow",
                "Fake",
            )

        registry.resolver.resolve = stall
        started = time.monotonic()
        with patch(
            "anistream.services.source_planner.PLAYER_PREFLIGHT_DEADLINE_SECONDS",
            0.01,
        ):
            plan = SourcePlanner(registry, FakeProbe()).plan(data, [1])

        self.assertLess(time.monotonic() - started, 0.08)
        self.assertFalse(plan.complete)
        self.assertIn("deadline", plan.preflight[0].detail)


if __name__ == "__main__":
    unittest.main()
