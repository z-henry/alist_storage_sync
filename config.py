import copy
import json
import os
import threading
from urllib.parse import urlsplit

from features.alist2strm.models import Alist2StrmTask


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


def _integer(value, path, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path} must be greater than or equal to {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{path} must be less than or equal to {maximum}")
    return value


def _boolean(value, path):
    if not isinstance(value, bool):
        raise ConfigError(f"{path} must be a boolean")
    return value


def _extension(value, path):
    value = _require_string(value, path).strip().lower()
    if not value.startswith("."):
        value = "." + value
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ConfigError(f"{path} must be a file extension")
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

    strm_tasks = _require_list(data.get("alist2strm_tasks", []), "alist2strm_tasks")
    data.setdefault("alist2strm_tasks", strm_tasks)
    seen_strm_ids = set()
    for index, task in enumerate(strm_tasks):
        path = f"alist2strm_tasks[{index}]"
        task = _require_dict(task, path)
        task_uuid = str(task.get("uuid", f"alist2strm-{index + 1}"))
        _require_string(task_uuid, f"{path}.uuid")
        if task_uuid in seen_strm_ids:
            raise ConfigError(f"duplicate Alist2Strm task uuid: {task_uuid}")
        seen_strm_ids.add(task_uuid)
        source_dir = _require_string(task.get("source_dir"), f"{path}.source_dir")
        if not source_dir.startswith("/"):
            raise ConfigError(f"{path}.source_dir must be an absolute AList path")
        target_dir = _require_string(task.get("target_dir"), f"{path}.target_dir")
        if os.path.isabs(target_dir) or ".." in target_dir.replace("\\", "/").split("/"):
            raise ConfigError(
                f"{path}.target_dir must stay relative to STRM_OUTPUT_ROOT"
            )
        _require_string(task.get("cron", "0 */6 * * *"), f"{path}.cron")
        mode = _require_string(task.get("mode", "alist_url"), f"{path}.mode").lower()
        if mode not in {"alist_url", "raw_url", "alist_path"}:
            raise ConfigError(
                f"{path}.mode must be one of alist_url, raw_url, alist_path"
            )
        task["mode"] = mode
        for key in ("flatten_mode", "subtitle", "image", "nfo", "overwrite"):
            task.setdefault(key, False)
            _boolean(task[key], f"{path}.{key}")
        extensions = _require_list(
            task.get("other_extensions", []), f"{path}.other_extensions"
        )
        task["other_extensions"] = [
            _extension(extension, f"{path}.other_extensions[{extension_index}]")
            for extension_index, extension in enumerate(extensions)
        ]
        task.setdefault("max_workers", 20)
        task.setdefault("max_downloaders", 3)
        _integer(task["max_workers"], f"{path}.max_workers", 1, 100)
        _integer(task["max_downloaders"], f"{path}.max_downloaders", 1, 20)
        if task["max_downloaders"] > task["max_workers"]:
            raise ConfigError(f"{path}.max_downloaders must not exceed max_workers")

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
    global alist2strm_tasks, strm_output_root
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

    strm_output_root = os.environ.get("STRM_OUTPUT_ROOT", "/media")
    alist2strm_tasks = [
        Alist2StrmTask(
            uuid=str(task.get("uuid", f"alist2strm-{index + 1}")),
            source_dir=task["source_dir"],
            target_dir=task["target_dir"],
            cron=task.get("cron", "0 */6 * * *"),
            flatten_mode=task.get("flatten_mode", False),
            subtitle=task.get("subtitle", False),
            image=task.get("image", False),
            nfo=task.get("nfo", False),
            mode=task.get("mode", "alist_url"),
            overwrite=task.get("overwrite", False),
            other_extensions=tuple(task.get("other_extensions", [])),
            max_workers=task.get("max_workers", 20),
            max_downloaders=task.get("max_downloaders", 3),
        )
        for index, task in enumerate(data.get("alist2strm_tasks", []))
    ]

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
