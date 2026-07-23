import tempfile
import unittest
from pathlib import Path

from anistream.services.local_library import LocalLibrary


def add_episode(root: Path, title: str, season: str, language: str, episode: int) -> Path:
    path = root / title / season / language / f"Episode {episode:03d}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 2048)
    return path


class LocalLibraryTests(unittest.TestCase):
    def test_scans_only_final_episode_mp4_files_and_groups_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            add_episode(root, "Title", "Season 1", "EN", 1)
            add_episode(root, "Title", "Season 1", "EN", 3)
            language = root / "Title" / "Season 1" / "EN"
            (language / ".Episode 002.part.mp4").write_bytes(b"x" * 2048)
            (language / "cover.jpg").write_bytes(b"x")

            entries = LocalLibrary(root).scan([])

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Title")
        self.assertEqual(entries[0].episodes, (1, 3))
        self.assertEqual(entries[0].status, "not_started")

    def test_matches_existing_history_and_resumes_the_next_local_episode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            add_episode(root, "Title", "Season 1", "EN", 1)
            add_episode(root, "Title", "Season 1", "EN", 4)
            history = {
                "provider_id": "site",
                "provider_name": "Site",
                "catalogue_url": "https://site/title/season/en/",
                "title": "Title",
                "season": "Season 1",
                "language": "EN",
                "language_code": "en",
                "total_episodes": 12,
                "current_episode": 3,
                "position": 120,
                "status": "in_progress",
                "updated_at": "2026-07-23T10:00:00+00:00",
            }

            entry = LocalLibrary(root).scan([history])[0]
            catalogue = entry.catalogue()

        self.assertEqual(entry.status, "in_progress")
        self.assertEqual(entry.resume_episode, 4)
        self.assertEqual(catalogue.provider_id, "site")
        self.assertEqual(catalogue.url, history["catalogue_url"])
        self.assertEqual(len(catalogue.episodes), 12)

    def test_in_progress_items_sort_before_new_and_completed_items(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            add_episode(root, "Active", "Season 1", "EN", 1)
            add_episode(root, "New", "Season 1", "EN", 1)
            add_episode(root, "Finished", "Season 1", "EN", 1)
            histories = [
                {
                    "title": "Active",
                    "season": "Season 1",
                    "language": "EN",
                    "current_episode": 1,
                    "status": "in_progress",
                },
                {
                    "title": "Finished",
                    "season": "Season 1",
                    "language": "EN",
                    "current_episode": 1,
                    "status": "completed",
                },
            ]

            entries = LocalLibrary(root).scan(histories)

        self.assertEqual([entry.title for entry in entries], ["Active", "New", "Finished"])


if __name__ == "__main__":
    unittest.main()
