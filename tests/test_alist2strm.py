import tempfile
import unittest
from pathlib import Path

from features.alist2strm.models import Alist2StrmTask, AlistEntry
from features.alist2strm.path_mapper import map_local_path, resolve_output_base
from features.alist2strm.trigger import build_strm_triggers


class OutputPathTests(unittest.TestCase):
    def test_maps_video_to_strm_and_preserves_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            base = resolve_output_base(directory, "movies")
            result = map_local_path(
                base,
                "/115/电影",
                "/115/电影/科幻/示例.mkv",
                flatten_mode=False,
            )
            self.assertEqual(result, Path(directory) / "movies/科幻/示例.strm")

    def test_flatten_mode_uses_only_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            base = resolve_output_base(directory, "flat")
            result = map_local_path(
                base,
                "/media",
                "/media/a/b/movie.mp4",
                flatten_mode=True,
            )
            self.assertEqual(result, Path(directory) / "flat/movie.strm")

    def test_rejects_target_escape_and_absolute_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                resolve_output_base(directory, "../escape")
            with self.assertRaises(ValueError):
                resolve_output_base(directory, "/absolute")

    def test_rejects_remote_path_outside_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                map_local_path(
                    Path(directory),
                    "/source",
                    "/another/movie.mkv",
                    flatten_mode=False,
                )


class AlistEntryTests(unittest.TestCase):
    def test_download_url_encodes_path_and_sign(self):
        entry = AlistEntry(
            server_url="https://alist.example",
            base_path="/",
            path="/媒体/电影 A.mkv",
            name="电影 A.mkv",
            size=123,
            is_dir=False,
            sign="abc:def",
        )
        self.assertEqual(
            entry.download_url,
            "https://alist.example/d/%E5%AA%92%E4%BD%93/%E7%94%B5%E5%BD%B1%20A.mkv?sign=abc%3Adef",
        )


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.task = Alist2StrmTask(
            uuid="movies",
            source_dir="/library",
            target_dir="movies",
        )

    def test_matches_only_paths_inside_source_directory(self):
        self.assertEqual(
            build_strm_triggers(
                [self.task],
                ["/library/movie.mkv", "/library-old/not-a-match.mkv"],
            ),
            [{"task_uuid": "movies", "paths": ["/library/movie.mkv"]}],
        )

    def test_deduplicates_and_normalizes_changed_paths(self):
        self.assertEqual(
            build_strm_triggers(
                [self.task],
                ["library/folder", "/library/folder", "/elsewhere/movie.mkv"],
            ),
            [{"task_uuid": "movies", "paths": ["/library/folder"]}],
        )


if __name__ == "__main__":
    unittest.main()
