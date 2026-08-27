import os
import tempfile
import unittest

import runtime_store


class OverviewGroupingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_path = runtime_store.DB_PATH
        self.original_initialized = runtime_store._INITIALIZED
        runtime_store.DB_PATH = os.path.join(self.tempdir.name, "runtime.db")
        runtime_store._INITIALIZED = False
        runtime_store.init_db()

    def tearDown(self):
        runtime_store.DB_PATH = self.original_path
        runtime_store._INITIALIZED = self.original_initialized
        self.tempdir.cleanup()

    def _empty_sync(self, trigger_type="scheduled"):
        run_id = runtime_store.create_run("sync-a", "sync", trigger_type)
        runtime_store.update_run(run_id, "succeeded", result={
            "source_found": True, "scanned_entries": 3,
            "directories_created": 0, "copy_requests_submitted": 0,
            "source_files_deleted": 0, "failures": 0,
        })

    def test_compacts_only_consecutive_scheduled_empty_runs(self):
        self._empty_sync()
        self._empty_sync()
        boundary = runtime_store.create_run("sync-a", "sync", "scheduled")
        runtime_store.update_run(boundary, "failed", error="expected")
        self._empty_sync()
        self._empty_sync("manual")

        entries = runtime_store.list_overview_entries(10, "sync", "sync-a")
        self.assertEqual([entry["overview_entry_type"] for entry in entries], ["run", "group", "run", "group"])
        self.assertEqual(entries[-1]["run_count"], 2)
        self.assertEqual(entries[1]["run_count"], 1)

    def test_compacts_busy_skips_separately(self):
        for _ in range(2):
            run_id = runtime_store.create_run("strm-a", "alist2strm", "scheduled")
            runtime_store.update_run(run_id, "skipped_busy", result={"reason": "test"})
        entries = runtime_store.list_overview_entries(10, "alist2strm", "strm-a")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["overview_kind"], "skipped_busy")
        self.assertEqual(entries[0]["run_count"], 2)


if __name__ == "__main__":
    unittest.main()
