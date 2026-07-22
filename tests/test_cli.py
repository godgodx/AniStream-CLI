import unittest

from anistream.cli import parse_episode_selection


class EpisodeSelectionTests(unittest.TestCase):
    def test_all_and_latest(self):
        self.assertEqual(parse_episode_selection("all", 4), [1, 2, 3, 4])
        self.assertEqual(parse_episode_selection("latest", 4), [4])

    def test_lists_ranges_and_duplicates(self):
        self.assertEqual(parse_episode_selection("1, 3-5, 4", 6), [1, 3, 4, 5])
        self.assertEqual(parse_episode_selection("5-3", 6), [3, 4, 5])

    def test_out_of_range_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_episode_selection("0,2,9", 4)


if __name__ == "__main__":
    unittest.main()
