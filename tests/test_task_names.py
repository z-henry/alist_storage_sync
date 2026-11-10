import copy
import importlib
import json
import os
import tempfile
from unittest.mock import Mock, patch

from flask import Flask

import runtime_store
from tests.test_config import BASE_CONFIG
from tests.test_runtime_store import RuntimeStoreTestCase


class TaskNameTests(RuntimeStoreTestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            with open(config_path, "w") as handle:
                json.dump(BASE_CONFIG, handle)
            with patch.dict(os.environ, {"CONFIG_PATH": config_path}):
                cls.config = importlib.import_module("config")
                cls.routes = importlib.import_module("ui_routes")
                cls.manager = importlib.import_module("task_manager")

    def setUp(self):
        super().setUp()
        self.original_config = self.config.get_config()
        self.addCleanup(self.config._apply_config, self.original_config)
        self.value = copy.deepcopy(BASE_CONFIG)
        self.value["tasks"] = [{"uuid": "3", "name": "MP 整理 → 115", "src": "/local", "dst": "/115"}]
        self.value["alist2strm_tasks"] = [{"uuid": "3", "name": "电影 STRM", "source_dir": "/115", "target_dir": "movies"}]
        self.value["dir_tree_build_tasks"] = [{"uuid": "3", "name": "115 缓存刷新", "src": "/115", "cron": "0 12 * * *", "qps": 0.1}]
        self.config._apply_config(self.config.validate_config(self.value))
        config_path = os.path.join(self.tempdir.name, "config.json")
        with open(config_path, "w") as handle:
            json.dump(self.value, handle)
        path_patch = patch.object(self.config, "CONFIG_PATH", config_path)
        path_patch.start()
        self.addCleanup(path_patch.stop)
        env_patch = patch.dict(os.environ, {"UI_USERNAME": "admin", "UI_PASSWORD": "test-password"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.app = Flask(__name__)
        self.app.secret_key = "isolated-test-secret"
        self.app.register_blueprint(self.routes.ui_blueprint)
        self.client = self.app.test_client()
        with self.app.app_context():
            fingerprint = self.routes._credential_fingerprint("admin", "test-password")
        with self.client.session_transaction() as session:
            session["ui_auth"] = fingerprint
            session["csrf_token"] = "test-csrf"

    def test_old_config_and_blank_names_keep_default_labels(self):
        for key in ("tasks", "alist2strm_tasks", "dir_tree_build_tasks"):
            self.value[key][0].pop("name")
        self.config._apply_config(self.config.validate_config(self.value))
        self.assertEqual(self.config.task_display_name("sync", "3"), "同步任务 3")
        self.assertEqual(self.config.task_display_name("alist2strm", "3"), "STRM 生成 3")
        self.assertEqual(self.config.task_display_name("dir_tree_build", "3"), "目录树刷新 3")
        self.value["tasks"][0]["name"] = "   "
        self.config._apply_config(self.config.validate_config(self.value))
        self.assertEqual(self.config.sync_tasks[0].name, "")
        self.assertEqual(self.config.task_display_name("sync", "3"), "同步任务 3")

    def test_names_are_normalized_and_invalid_types_rejected(self):
        for key in ("tasks", "alist2strm_tasks", "dir_tree_build_tasks"):
            with self.subTest(key=key):
                value = copy.deepcopy(self.value)
                value[key][0]["name"] = "  自定义任务  "
                self.assertEqual(self.config.validate_config(value)[key][0]["name"], "自定义任务")
                value[key][0]["name"] = 123
                with self.assertRaises(self.config.ConfigError):
                    self.config.validate_config(value)

    def test_same_uuid_in_different_task_types_keeps_its_own_name(self):
        with patch.object(self.manager, "scheduler_jobs", return_value=[]):
            tasks = self.client.get("/ui/api/tasks").get_json()["tasks"]
        names = {task["task_type"]: (task["task_uuid"], task["name"]) for task in tasks}
        self.assertEqual(names["sync"], ("3", "MP 整理 → 115"))
        self.assertEqual(names["alist2strm"], ("3", "电影 STRM"))
        self.assertEqual(names["dir_tree_build"], ("3", "115 缓存刷新"))

    def test_rename_updates_overview_history_and_detail_without_changing_ids(self):
        run_id = runtime_store.create_run("3", "sync", "api", parameters={"task_name": "旧名称"})
        runtime_store.update_run(run_id, "succeeded")
        for name in ("新名称", ""):
            self.value["tasks"][0]["name"] = name
            self.config._apply_config(self.config.validate_config(self.value))
            expected = name or "同步任务 3"
            detail = self.client.get(f"/ui/api/runs/{run_id}").get_json()["run"]
            history = self.client.get("/ui/api/runs?task_uuid=3&task_type=sync").get_json()["runs"]
            with patch.object(self.manager, "runtime_snapshot", return_value={}):
                overview = self.client.get("/ui/api/overview").get_json()["recent_activity"]
            for run in (detail, history[0], overview[0]):
                self.assertEqual(run["task_name"], expected)
                self.assertEqual(run["run_id"], run_id)
                self.assertEqual(run["task_uuid"], "3")

    def test_save_and_reload_persist_name_without_changing_api_identity(self):
        self.value["tasks"][0]["name"] = "  新名称  "
        with patch.object(self.manager, "reload_scheduler", return_value={"online": True}):
            response = self.client.put("/ui/api/config", json=self.value, headers={"X-CSRF-Token": "test-csrf"})
        self.assertEqual(response.status_code, 200)
        self.config.load_config()
        self.assertEqual(self.config.sync_tasks[0].name, "新名称")
        self.assertEqual(self.config.sync_tasks[0].uuid, "3")
        self.assertEqual(self.config.get_config()["tasks"][0]["name"], "新名称")

    def test_scheduled_jobs_use_name_and_keep_same_job_ids(self):
        scheduler = Mock()
        with patch.object(self.manager, "scheduler", scheduler):
            self.manager._add_scheduler_jobs()
        jobs = {call.kwargs["id"]: call.kwargs["name"] for call in scheduler.add_job.call_args_list}
        self.assertEqual(jobs["sync:3"], "MP 整理 → 115")
        self.assertEqual(jobs["alist2strm:3"], "电影 STRM")
        self.assertEqual(jobs["dir-tree:3"], "115 缓存刷新")

    def test_queued_runs_snapshot_name_for_removed_task_history(self):
        with patch.object(self.manager.api_alist, "is_online", return_value=False), \
             patch.object(self.manager.api_alist, "health_snapshot", return_value={}):
            run_id = self.manager.check_tasks(self.config.sync_tasks[0], False, "api")
        self.config._apply_config(self.config.validate_config(BASE_CONFIG))
        detail = self.client.get(f"/ui/api/runs/{run_id}").get_json()["run"]
        self.assertEqual(detail["task_name"], "MP 整理 → 115")
        self.assertEqual(detail["task_uuid"], "3")
