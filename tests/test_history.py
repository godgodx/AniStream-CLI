import tempfile
import unittest
from pathlib import Path

from anistream.services.history import HistoryStore


class HistoryTests(unittest.TestCase):
    def test_progress_and_completion_are_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            store = HistoryStore(path)
            values = dict(
                provider_id="site",
                catalogue_url="https://site/title/season/en/",
                title="Title",
                season="Season 1",
                language="EN",
                episode=2,
                duration=1200,
            )
            store.update(**values, position=321, completed=False)
            resumed = HistoryStore(path).get("site", values["catalogue_url"])
            self.assertEqual(resumed["current_episode"], 2)
            self.assertEqual(resumed["position"], 321)
            store.update(**values, position=1199, completed=True)
            finished = HistoryStore(path).get("site", values["catalogue_url"])
            self.assertEqual(finished["current_episode"], 3)
            self.assertIn(2, finished["seen_episodes"])


if __name__ == "__main__":
    unittest.main()
