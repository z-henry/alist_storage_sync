import copy
import importlib
import json
import os
import tempfile
import unittest


BASE_CONFIG = {
    "tasks": [],
    "alist": {"url": "https://alist.example", "apikey": "test-key"},
    "cover_dst_when_diff": False,
    "delete_src_when_same": False,
    "emby": {"enabled": False, "url": "", "apikey": "", "mount_path": ""},
    "webhook": {"enabled": False, "url": ""},
    "dir_tree_build_tasks": [],
}


class ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        )
        json.dump(BASE_CONFIG, handle)
        handle.close()
        cls.config_path = handle.name
        os.environ["CONFIG_PATH"] = cls.config_path
        cls.config = importlib.import_module("config")

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.config_path)

    def test_old_config_defaults_to_no_strm_tasks(self):
        self.assertEqual(self.config.alist2strm_tasks, [])
        self.assertEqual(self.config.get_config()["alist2strm_tasks"], [])

    def test_normalizes_extensions(self):
        value = copy.deepcopy(BASE_CONFIG)
        value["alist2strm_tasks"] = [
            {
                "uuid": "strm",
                "source_dir": "/movies",
                "target_dir": "movies",
                "other_extensions": ["XML", ".txt"],
            }
        ]
        result = self.config.validate_config(value)
        self.assertEqual(
            result["alist2strm_tasks"][0]["other_extensions"],
            [".xml", ".txt"],
        )

    def test_rejects_output_escape(self):
        value = copy.deepcopy(BASE_CONFIG)
        value["alist2strm_tasks"] = [
            {
                "uuid": "strm",
                "source_dir": "/movies",
                "target_dir": "../outside",
            }
        ]
        with self.assertRaises(self.config.ConfigError):
            self.config.validate_config(value)


if __name__ == "__main__":
    unittest.main()
