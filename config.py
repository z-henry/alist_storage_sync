import copy
import json
import os
import threading
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


class Task:
    def __init__(self, uuid, src, dst, cron="1 * * * *", mounted_path=""):
        self.src = src
        self.dst = dst
        self.cron = cron
        self.uuid = uuid
        self.mounted_path = mounted_path


class DirTreeBuildTask:
    def __init__(self, uuid, src, cron, qps, run_at_start=False):
        self.uuid = uuid
        self.src = src
        self.cron = cron
        self.qps = qps
        self.run_at_start = run_at_start


CONFIG_PATH = os.environ.get(
    "CONFIG_PATH", os.path.join(os.path.dirname(__file__), "config.json")
)
_CONFIG_LOCK = threading.RLock()
_raw_config = {}


def _require_dict(value, path):
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be an object")
    return value


def _require_list(value, path):
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be an array")
    return value


def _require_string(value, path, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigError(f"{path} must be a non-empty string")
    return value


def _number(value, path, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a number")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path} must be greater than or equal to {minimum}")
    return value


def _validate_url(value, path, allow_empty=False):
    value = _require_string(value, path, allow_empty=allow_empty)
    if not value and allow_empty:
        return value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"{path} must be an http or https URL")
    return value


def validate_config(value):
    data = copy.deepcopy(_require_dict(value, "config"))
    tasks = _require_list(data.get("tasks"), "tasks")
    seen_sync_ids = set()
    for index, task in enumerate(tasks):
        path = f"tasks[{index}]"
        task = _require_dict(task, path)
        task_uuid = str(task.get("uuid", index + 1))
        if task_uuid in seen_sync_ids:
            raise ConfigError(f"duplicate sync task uuid: {task_uuid}")
        seen_sync_ids.add(task_uuid)
        _require_string(task.get("src"), f"{path}.src")
        _require_string(task.get("dst"), f"{path}.dst")
        _require_string(task.get("cron", "1 * * * *"), f"{path}.cron")
        if "mounted_path" in task:
            _require_string(task["mounted_path"], f"{path}.mounted_path", allow_empty=True)

    alist = _require_dict(data.get("alist"), "alist")
    _validate_url(alist.get("url"), "alist.url")
    _require_string(alist.get("apikey"), "alist.apikey")
    for key, default, minimum in (
        ("task_missing_timeout_seconds", 600, 0),
        ("request_timeout_seconds", 15, 1),
        ("healthcheck_interval_seconds", 15, 5),
        ("healthcheck_timeout_seconds", 3, 1),
    ):
        _number(alist.get(key, default), f"alist.{key}", minimum)
        alist.setdefault(key, default)

    for key in ("cover_dst_when_diff", "delete_src_when_same"):
        if not isinstance(data.get(key), bool):
            raise ConfigError(f"{key} must be a boolean")

    emby = _require_dict(data.get("emby"), "emby")
    if not isinstance(emby.get("enabled"), bool):
        raise ConfigError("emby.enabled must be a boolean")
    _validate_url(emby.get("url", ""), "emby.url", allow_empty=not emby["enabled"])
    _require_string(emby.get("apikey", ""), "emby.apikey", allow_empty=not emby["enabled"])
    _require_string(emby.get("mount_path", ""), "emby.mount_path", allow_empty=True)

    webhook = _require_dict(data.get("webhook"), "webhook")
    if not isinstance(webhook.get("enabled"), bool):
        raise ConfigError("webhook.enabled must be a boolean")
    _validate_url(
        webhook.get("url", ""),
        "webhook.url",
        allow_empty=not webhook["enabled"],
    )

    tree_tasks = _require_list(data.get("dir_tree_build_tasks", []), "dir_tree_build_tasks")
    seen_tree_ids = set()
    for index, task in enumerate(tree_tasks):
        path = f"dir_tree_build_tasks[{index}]"
        task = _require_dict(task, path)
        task_uuid = str(task.get("uuid", f"dir_tree_build-{index + 1}"))
        if task_uuid in seen_tree_ids:
            raise ConfigError(f"duplicate dir tree task uuid: {task_uuid}")
        seen_tree_ids.add(task_uuid)
        _require_string(task.get("src"), f"{path}.src")
        _require_string(task.get("cron"), f"{path}.cron")
        _number(task.get("qps"), f"{path}.qps", 0.000001)
        if "run_at_start" in task and not isinstance(task["run_at_start"], bool):
            raise ConfigError(f"{path}.run_at_start must be a boolean")
    return data


def _apply_config(data):
    global _raw_config
    global authorization, alist_url
    global alist_task_missing_timeout_seconds, alist_request_timeout_seconds
    global alist_healthcheck_interval_seconds, alist_healthcheck_timeout_seconds
    global sync_tasks, cover_dst_when_diff, delete_src_when_same
    global emby_enable, emby_url, emby_apikey, emby_mount_path
    global webhook_enable, webhook_url, dir_tree_build_tasks

    alist = data["alist"]
    authorization = alist["apikey"]
    alist_url = alist["url"].rstrip("/")
    alist_task_missing_timeout_seconds = max(
        0, int(alist.get("task_missing_timeout_seconds", 600))
    )
    alist_request_timeout_seconds = max(
        1, float(alist.get("request_timeout_seconds", 15))
    )
    alist_healthcheck_interval_seconds = max(
        5, float(alist.get("healthcheck_interval_seconds", 15))
    )
    alist_healthcheck_timeout_seconds = max(
        1, float(alist.get("healthcheck_timeout_seconds", 3))
    )
    sync_tasks = [
        Task(
            str(task.get("uuid", index + 1)),
            task["src"],
            task["dst"],
            task.get("cron", "1 * * * *"),
            task.get("mounted_path", ""),
        )
        for index, task in enumerate(data["tasks"])
    ]
    cover_dst_when_diff = data["cover_dst_when_diff"]
    delete_src_when_same = data["delete_src_when_same"]

    emby = data["emby"]
    emby_enable = emby["enabled"]
    emby_url = emby.get("url", "").rstrip("/")
    emby_apikey = emby.get("apikey", "")
    emby_mount_path = emby.get("mount_path", "")

    webhook = data["webhook"]
    webhook_enable = webhook["enabled"]
    webhook_url = webhook.get("url", "")

    dir_tree_build_tasks = [
        DirTreeBuildTask(
            str(task.get("uuid", f"dir_tree_build-{index + 1}")),
            task["src"],
            task["cron"],
            task["qps"],
            task.get("run_at_start", False),
        )
        for index, task in enumerate(data.get("dir_tree_build_tasks", []))
    ]
    _raw_config = copy.deepcopy(data)


def load_config():
    with _CONFIG_LOCK:
        with open(CONFIG_PATH, "r", encoding="utf8") as config_file:
            data = json.load(config_file)
        data = validate_config(data)
        _apply_config(data)
        return copy.deepcopy(data)


def get_config():
    with _CONFIG_LOCK:
        return copy.deepcopy(_raw_config)


def save_config(value):
    data = validate_config(value)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with _CONFIG_LOCK:
        with open(CONFIG_PATH, "r", encoding="utf8") as config_file:
            previous = config_file.read()
        try:
            with open(CONFIG_PATH, "w", encoding="utf8") as config_file:
                config_file.write(serialized)
                config_file.flush()
                os.fsync(config_file.fileno())
        except OSError:
            try:
                with open(CONFIG_PATH, "w", encoding="utf8") as config_file:
                    config_file.write(previous)
                    config_file.flush()
                    os.fsync(config_file.fileno())
            except OSError:
                pass
            raise
        _apply_config(data)
    return copy.deepcopy(data)


load_config()
