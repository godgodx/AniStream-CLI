import json
import tempfile
import unittest
from pathlib import Path

from anistream.services.history import HistoryStore


class HistoryTests(unittest.TestCase):
    def test_languages_keep_independent_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            store = HistoryStore(path)
            shared = dict(
                provider_id="site",
                provider_name="Site",
                title="Title",
                season="Season 1",
                total_episodes=12,
                duration=1200,
                completed=False,
            )
            store.update(
                **shared,
                catalogue_url="https://site/title/season/vf/",
                language="VF",
                language_code="vf",
                episode=2,
                position=100,
            )
            store.update(
                **shared,
                catalogue_url="https://site/title/season/vostfr/",
                language="VOSTFR",
                language_code="vostfr",
                episode=5,
                position=200,
            )

            vf = store.get("site", "https://site/title/season/vf/")
            vostfr = store.get("site", "https://site/title/season/vostfr/")
            self.assertEqual((vf["current_episode"], vf["position"]), (2, 100))
            self.assertEqual((vostfr["current_episode"], vostfr["position"]), (5, 200))
            self.assertEqual(len(store.all()), 2)

    def test_series_progress_and_title_completion_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            store = HistoryStore(path)
            values = dict(
                provider_id="site",
                provider_name="Site",
                catalogue_url="https://site/title/season/en/",
                title="Title",
                season="Season 1",
                language="EN",
                total_episodes=12,
                duration=1200,
            )
            store.update(**values, episode=2, position=321, completed=False, language_code="en")
            resumed = HistoryStore(path).get("site", values["catalogue_url"])
            self.assertEqual(resumed["current_episode"], 2)
            self.assertEqual(resumed["position"], 321)
            self.assertEqual(resumed["status"], "in_progress")
            self.assertEqual(resumed["language_code"], "en")

            store.update(**values, episode=2, position=1199, completed=True)
            next_episode = HistoryStore(path).get("site", values["catalogue_url"])
            self.assertEqual(next_episode["current_episode"], 3)
            self.assertEqual(next_episode["status"], "in_progress")
            self.assertIn(2, next_episode["seen_episodes"])

            store.update(**values, episode=12, position=1199, completed=True)
            finished = HistoryStore(path).get("site", values["catalogue_url"])
            self.assertEqual(finished["current_episode"], 12)
            self.assertEqual(finished["status"], "completed")

    def test_movie_is_completed_only_after_playback_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            store = HistoryStore(path)
            values = dict(
                provider_id="site",
                provider_name="Site",
                catalogue_url="https://site/movie/en/",
                title="Movie",
                season="Movie",
                language="EN",
                episode=1,
                total_episodes=1,
                duration=5400,
            )
            store.update(**values, position=600, completed=False)
            self.assertEqual(store.get("site", values["catalogue_url"])["status"], "in_progress")
            store.update(**values, position=5399, completed=True)
            finished = store.get("site", values["catalogue_url"])
            self.assertEqual(finished["status"], "completed")
            self.assertEqual(finished["media_type"], "movie")

    def test_catalogue_sync_migrates_legacy_completion_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            url = "https://site/title/season/en/"
            key = HistoryStore(path).key("site", url)
            path.write_text(
                json.dumps(
                    {
                        key: {
                            "provider_id": "site",
                            "catalogue_url": url,
                            "title": "Old title",
                            "current_episode": 2,
                            "status": "completed",
                            "seen_episodes": [1],
                            "updated_at": "2026-01-01T00:00:00+00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )
            store = HistoryStore(path)
            synced = store.sync_catalogue(
                provider_id="site",
                provider_name="Site",
                catalogue_url=url,
                title="Current title",
                season="Season 1",
                language="EN",
                total_episodes=12,
            )
            self.assertEqual(synced["status"], "in_progress")
            self.assertEqual(synced["media_type"], "series")
            self.assertEqual(synced["total_episodes"], 12)
            self.assertEqual(synced["updated_at"], "2026-01-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
