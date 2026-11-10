import os
import tempfile
import unittest

import runtime_store


class RuntimeStoreTestCase(unittest.TestCase):
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


class OverviewGroupingTests(RuntimeStoreTestCase):
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

    def test_busy_skips_merge_across_api_and_scheduled_triggers(self):
        for trigger in ("scheduled", "api", "api"):
            run_id = runtime_store.create_run("sync-a", "sync", trigger)
            runtime_store.update_run(run_id, "skipped_busy")
        entries = runtime_store.list_overview_entries(10, "sync", "sync-a")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["run_count"], 3)
        self.assertEqual(entries[0]["trigger_type"], "mixed")
        self.assertEqual(len(runtime_store.list_runs()), 3)

    def test_api_busy_groups_keep_real_runs_as_boundaries(self):
        for status in ("skipped_busy", "skipped_busy", "succeeded", "skipped_busy"):
            run_id = runtime_store.create_run("sync-a", "sync", "api")
            runtime_store.update_run(run_id, status)
        entries = runtime_store.list_overview_entries(10, "sync", "sync-a")
        self.assertEqual([e["overview_entry_type"] for e in entries], ["group", "run", "group"])
        self.assertEqual(entries[-1]["run_count"], 2)

    def test_startup_regroups_old_api_busy_records_once(self):
        run_ids = []
        for trigger in ("scheduled", "api", "scheduled"):
            run_id = runtime_store.create_run("sync-a", "sync", trigger)
            runtime_store.update_run(run_id, "skipped_busy")
            run_ids.append(run_id)
        with runtime_store._connect() as connection:
            connection.execute("PRAGMA user_version = 0")
            connection.execute("UPDATE task_runs SET overview_kind = 'raw', overview_group_id = NULL WHERE trigger_type = 'api'")
        for _ in range(2):
            runtime_store._INITIALIZED = False
            runtime_store.init_db()
            entries = runtime_store.list_overview_entries(10, "sync", "sync-a")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["run_count"], 3)
            self.assertEqual(entries[0]["trigger_type"], "mixed")
        self.assertEqual({r["run_id"] for r in runtime_store.list_runs()}, set(run_ids))

    def test_source_deletion_is_not_an_empty_run(self):
        run_id = runtime_store.create_run("sync-a", "sync", "scheduled")
        runtime_store.update_run(run_id, "succeeded", result={
            "source_found": True, "scanned_entries": 12,
            "directories_created": 0, "copy_requests_submitted": 0,
            "source_files_deleted": 12, "failures": 0,
        })
        entry = runtime_store.list_overview_entries(10, "sync", "sync-a")[0]
        self.assertEqual(entry["overview_entry_type"], "run")


class SourceDeletionTests(RuntimeStoreTestCase):
    def test_deletion_details_are_persisted_paginated_and_isolated(self):
        run_id = runtime_store.create_run("sync-a", "sync", "scheduled")
        other = runtime_store.create_run("sync-b", "sync", "api")
        for name, success in (("一.mp4", True), ("二.mp4", False), ("三.mp4", True)):
            runtime_store.record_source_file_deletion(run_id, "/source", "/115", name, 100, success)
        runtime_store.record_source_file_deletion(other, "/other", "/115", "other", 1, True)
        page = runtime_store.get_run(run_id, child_limit=2)
        self.assertEqual(page["source_deletion_summary"], {"total": 3, "succeeded": 2, "failed": 1})
        self.assertEqual([r["entry_name"] for r in page["source_deletions"]], ["一.mp4", "二.mp4"])
        self.assertEqual(page["source_deletions"][1]["status"], "failed")
        self.assertTrue(page["source_deletions"][1]["error"])
        self.assertTrue(page["source_deletions_page"]["truncated"])
        page = runtime_store.get_run(run_id, child_limit=2, child_offset=2)
        self.assertEqual([r["entry_name"] for r in page["source_deletions"]], ["三.mp4"])
        self.assertFalse(page["source_deletions_page"]["truncated"])


class CopyPostprocessTests(RuntimeStoreTestCase):
    def _run_with_copies(self, status="failed", result=None, error="scan failed"):
        run_id = runtime_store.create_run("sync-a", "sync", "api")
        runtime_store.record_alist_copy_tasks(run_id, [
            {"id": "a", "state": 1}, {"id": "b", "state": 1},
        ], "/source", "/115", "file.mp4")
        runtime_store.update_run(run_id, status, result=result, error=error)
        return run_id

    def test_failed_run_waits_for_all_copies_then_cleans_once(self):
        run_id = self._run_with_copies(result={"failures": 5, "source_files_deleted": 43})
        original = runtime_store.get_run(run_id)
        runtime_store.reconcile_alist_copy_tasks(
            [{"id": "a", "state": 2, "progress": 100}], [{"id": "b", "state": 1}],
        )
        self.assertEqual(runtime_store.claim_pending_postprocess_runs(), [])
        stats = runtime_store.reconcile_alist_copy_tasks([
            {"id": "a", "state": 2, "progress": 100},
            {"id": "b", "state": 2, "progress": 100},
        ], [])
        self.assertEqual(stats["finalized_runs"][0]["status"], "failed")
        finished = runtime_store.get_run(run_id)
        self.assertEqual(finished["error"], original["error"])
        self.assertEqual(finished["finished_at"], original["finished_at"])
        self.assertEqual(finished["result"]["failures"], 5)
        self.assertEqual(finished["alist_task_summary"]["succeeded"], 2)
        pending = runtime_store.claim_pending_postprocess_runs()
        self.assertEqual([r["run_id"] for r in pending], [run_id])
        self.assertEqual(len(pending[0]["tasks"]), 2)
        self.assertEqual(runtime_store.claim_pending_postprocess_runs(), [])
        runtime_store.finish_run_postprocess(run_id, True, {"cleanup": {"requested": 2}})
        self.assertEqual(runtime_store.finalize_waiting_alist_runs(), [])
        self.assertEqual(runtime_store.get_run(run_id)["status"], "failed")

    def test_restart_retries_failed_cleanup_without_changing_parent_failure(self):
        run_id = self._run_with_copies()
        runtime_store.reconcile_alist_copy_tasks([
            {"id": "a", "state": 2}, {"id": "b", "state": 2},
        ], [])
        runtime_store.claim_pending_postprocess_runs()
        runtime_store.finish_run_postprocess(run_id, False, {"cleanup": {"success": False}})
        runtime_store._INITIALIZED = False
        runtime_store.init_db()
        pending = runtime_store.claim_pending_postprocess_runs()
        self.assertEqual([r["run_id"] for r in pending], [run_id])
        self.assertEqual(pending[0]["status"], "failed")
        runtime_store.finish_run_postprocess(run_id, True, {"cleanup": {"success": True}})
        self.assertEqual(runtime_store.get_run(run_id)["status"], "failed")
        runtime_store._INITIALIZED = False
        runtime_store.init_db()
        self.assertEqual(runtime_store.claim_pending_postprocess_runs(), [])

    def test_copy_success_does_not_hide_sync_failure(self):
        run_id = self._run_with_copies("waiting_alist", {"failures": 1}, error=None)
        runtime_store.reconcile_alist_copy_tasks([
            {"id": "a", "state": 2}, {"id": "b", "state": 2},
        ], [])
        self.assertEqual(runtime_store.get_run(run_id)["status"], "failed")
        self.assertEqual(len(runtime_store.claim_pending_postprocess_runs()), 1)

    def test_missing_task_in_failed_run_times_out(self):
        run_id = self._run_with_copies()
        with runtime_store._connect() as connection:
            connection.execute("UPDATE alist_copy_tasks SET last_seen_at = '2000-01-01T00:00:00+00:00'")
        stats = runtime_store.reconcile_alist_copy_tasks([], [], missing_timeout_seconds=1)
        self.assertEqual(stats["missing_timed_out"], 2)
        pending = runtime_store.claim_pending_postprocess_runs()
        self.assertEqual(pending[0]["run_id"], run_id)
        self.assertTrue(all(t["status"] == "missing_timeout" for t in pending[0]["tasks"]))
        self.assertEqual(runtime_store.get_run(run_id)["error"], "scan failed")

    def test_restart_recovers_copies_from_interrupted_run(self):
        run_id = self._run_with_copies("running")
        runtime_store._INITIALIZED = False
        runtime_store.init_db()
        runtime_store.reconcile_alist_copy_tasks([
            {"id": "a", "state": 2}, {"id": "b", "state": 2},
        ], [])
        self.assertEqual(runtime_store.get_run(run_id)["status"], "interrupted")
        self.assertEqual(runtime_store.claim_pending_postprocess_runs()[0]["run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
