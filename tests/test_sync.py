import importlib
import json
import os
import tempfile
from unittest.mock import patch

import runtime_store
from tests.test_config import BASE_CONFIG
from tests.test_runtime_store import RuntimeStoreTestCase


class SyncDeletionTests(RuntimeStoreTestCase):
    @classmethod
    def setUpClass(cls):
        # Import against a test config, never live credentials.
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            with open(config_path, "w") as handle:
                json.dump(BASE_CONFIG, handle)
            with patch.dict(os.environ, {"CONFIG_PATH": config_path}):
                cls.sync = importlib.import_module("sync")
                cls.manager = importlib.import_module("task_manager")

    def test_records_successful_and_failed_source_deletions(self):
        run_id = runtime_store.create_run("sync-a", "sync", "scheduled")
        files = [
            {"name": "one.mp4", "size": 100, "is_dir": False},
            {"name": "two.mp4", "size": 200, "is_dir": False},
        ]
        with patch.object(self.sync, "list_files", return_value=files), \
             patch.object(self.sync.config, "delete_src_when_same", True), \
             patch.object(self.sync, "remove_file", side_effect=[True, False]) as remove:
            result = self.sync.sync_files("/local", "/115", run_id=run_id)
        self.assertEqual(result["source_files_deleted"], 1)
        self.assertEqual(result["source_files_delete_failed"], 1)
        self.assertEqual(result["failures"], 1)
        self.assertEqual([call.args[0] for call in remove.call_args_list], ["/local", "/local"])
        detail = runtime_store.get_run(run_id)
        self.assertEqual([r["status"] for r in detail["source_deletions"]], ["succeeded", "failed"])
        self.assertEqual(detail["source_deletions"][0]["destination_dir"], "/115")

    def test_failed_sync_still_cleans_completed_openlist_records(self):
        run_id = runtime_store.create_run("sync-a", "sync", "api")
        task = self.sync.config.Task("sync-a", "/local", "/115")
        existing = {"name": "existing.mp4", "size": 100, "is_dir": False}
        new = {"name": "new.mp4", "size": 200, "is_dir": False}
        with patch.object(self.sync, "get_files", return_value={"is_dir": True}), \
             patch.object(self.sync, "list_files", side_effect=[[existing, new], [existing]]), \
             patch.object(self.sync.config, "delete_src_when_same", True), \
             patch.object(self.sync, "remove_file", return_value=False), \
             patch.object(self.sync, "copy_file", return_value=[{"id": "copy-new", "state": 1}]):
            outcome = self.manager.execute_check_tasks(task, False, run_id=run_id)
        self.assertEqual(outcome.status, "failed")
        runtime_store.update_run(run_id, outcome.status, result=outcome.result)
        # Inspection cleans the tracked copy record, keeps the batch failed,
        # and does not run successful-batch callbacks or repeat cleanup.
        with patch.object(self.manager.api_alist, "copy_done", return_value=[{
                "id": "copy-new", "state": 2, "progress": 100,
             }]), \
             patch.object(self.manager.api_alist, "copy_undone", return_value=[]), \
             patch.object(self.manager.api_alist, "copy_delete_tasks", return_value=True) as cleanup, \
             patch.object(self.manager, "perform_cache_refresh") as refresh:
            self.manager.execute_check_cache_refresh()
            cleanup.assert_called_once_with(["copy-new"])
            refresh.assert_not_called()
            self.manager.execute_check_cache_refresh()
            cleanup.assert_called_once()
        detail = runtime_store.get_run(run_id)
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["postprocess_status"], "succeeded")
        self.assertEqual(detail["result"]["postprocess"]["cleanup"]["requested"], 1)
        self.assertEqual(len(detail["alist_tasks"]), 1)


    def test_record_cleanup_accepts_only_explicit_task_not_found_errors(self):
        api = self.manager.api_alist
        for errors, expected in [
            ({}, True),
            ({"a": "task not found"}, True),
            ({"a": "task not found", "b": "permission denied"}, False),
            ({"a": "database unavailable"}, False),
        ]:
            with self.subTest(errors=errors):
                with patch.object(api, "_request") as request, \
                     patch.object(api, "parse_json_response", return_value={"code": 200, "data": errors}):
                    self.assertEqual(api.copy_delete_tasks(["a", "b"]), expected)
                    request.assert_called_once_with("POST", "/api/task/copy/delete_some", json=["a", "b"])
        with patch.object(api, "_request"), \
             patch.object(api, "parse_json_response", return_value={"code": 500, "message": "task not found"}):
            self.assertFalse(api.copy_delete_tasks(["a"]))
