import copy
import json
import threading
from datetime import datetime, timezone
from time import perf_counter

import requests

import config
import logger_config


_HEALTH_LOCK = threading.Lock()
_HEALTH = {
    "online": False,
    "reachable": False,
    "checked_at": None,
    "latency_ms": None,
    "error": "AList health check has not run yet",
    "url": None,
}


def _headers():
    return {
        "Authorization": config.authorization,
        "Content-Type": "application/json",
    }


def _set_health(online, reachable, error=None, latency_ms=None):
    with _HEALTH_LOCK:
        _HEALTH.update(
            {
                "online": bool(online),
                "reachable": bool(reachable),
                "checked_at": datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"
                ),
                "latency_ms": latency_ms,
                "error": str(error)[:2000] if error else None,
                "url": config.alist_url,
            }
        )


def health_snapshot():
    with _HEALTH_LOCK:
        return copy.deepcopy(_HEALTH)


def is_online():
    return health_snapshot()["online"]


def check_health():
    started = perf_counter()
    timeout = config.alist_healthcheck_timeout_seconds
    try:
        ping = requests.get(f"{config.alist_url}/ping", timeout=timeout)
        latency_ms = round((perf_counter() - started) * 1000)
        if ping.status_code != 200 or ping.text.strip().lower() != "pong":
            error = f"AList ping returned HTTP {ping.status_code}: {ping.text[:200]}"
            _set_health(False, True, error, latency_ms)
            return health_snapshot()

        task_response = requests.get(
            f"{config.alist_url}/api/task/copy/undone",
            headers=_headers(),
            timeout=timeout,
        )
        latency_ms = round((perf_counter() - started) * 1000)
        payload = parse_json_response(task_response, log_error=False)
        if task_response.status_code != 200 or not payload or payload.get("code") != 200:
            message = payload.get("message") if payload else task_response.text[:200]
            _set_health(
                False,
                True,
                f"AList task API is unavailable: {message}",
                latency_ms,
            )
            return health_snapshot()

        _set_health(True, True, latency_ms=latency_ms)
    except requests.RequestException as error:
        _set_health(
            False,
            False,
            error,
            round((perf_counter() - started) * 1000),
        )
    return health_snapshot()


def _request(method, path, **kwargs):
    kwargs.setdefault("headers", _headers())
    kwargs.setdefault("timeout", config.alist_request_timeout_seconds)
    try:
        return requests.request(method, f"{config.alist_url}{path}", **kwargs)
    except requests.RequestException as error:
        _set_health(False, False, error)
        logger_config.logger.error(f"AList request failed: {method} {path}: {error}")
        return None


def parse_json_response(response, log_error=True):
    if response is None:
        return None
    try:
        return json.loads(response.text)
    except (json.JSONDecodeError, TypeError) as error:
        if log_error:
            logger_config.logger.error(f"Failed to parse JSON response: {error}")
        return None


def _task_list(path, label):
    response = _request("GET", path)
    text = parse_json_response(response)
    if text and text.get("code") == 200:
        return text.get("data", [])
    message = text.get("message") if text else "invalid JSON response"
    logger_config.logger.error(
        f"Failed to get {label} copy from {config.alist_url}: {message}"
    )
    return None


def copy_done():
    return _task_list("/api/task/copy/done", "done")


def copy_undone():
    return _task_list("/api/task/copy/undone", "undone")


def list_files(path, refresh=False):
    response = _request(
        "POST",
        "/api/fs/list",
        json={"path": path, "refresh": refresh},
    )
    text = parse_json_response(response)
    if text and text.get("code") == 200:
        return text.get("data", {}).get("content", []) or []
    message = text.get("message") if text else "invalid JSON response"
    logger_config.logger.error(
        f"Failed to list files from {config.alist_url} at {path}: {message}"
    )
    return None


def get_files(path, refresh=False):
    response = _request(
        "POST",
        "/api/fs/get",
        json={"path": path, "refresh": refresh},
    )
    text = parse_json_response(response)
    if text and text.get("code") == 200:
        return text.get("data", {})
    message = text.get("message") if text else "invalid JSON response"
    logger_config.logger.error(
        f"Failed to get files from {config.alist_url} at {path}: {message}"
    )
    return None


def copy_file(src_dir, dst_dir, file_name):
    response = _request(
        "POST",
        "/api/fs/copy",
        json={"src_dir": src_dir, "dst_dir": dst_dir, "names": [file_name]},
    )
    text = parse_json_response(response)
    if not text or text.get("code") != 200:
        message = text.get("message") if text else "invalid JSON response"
        logger_config.logger.error(
            f"Failed to copy {file_name} from {src_dir} to {dst_dir}: {message}"
        )
        return None

    data = text.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(
            "AList /api/fs/copy returned an unexpected response: data must be an object"
        )

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise RuntimeError(
            "AList /api/fs/copy returned an unexpected response: data.tasks must be a list"
        )
    for task in tasks:
        if not isinstance(task, dict) or not task.get("id"):
            raise RuntimeError(
                "AList /api/fs/copy returned a task without an id; a newer AList/OpenList is required"
            )
    return tasks


def copy_delete_tasks(task_ids):
    if not task_ids:
        return True
    response = _request(
        "POST",
        "/api/task/copy/delete_some",
        json=task_ids,
    )
    text = parse_json_response(response)
    if not text or text.get("code") != 200:
        message = text.get("message") if text else "invalid JSON response"
        logger_config.logger.error(f"Failed to delete completed copy tasks: {message}")
        return False
    errors = text.get("data") or {}
    if errors:
        logger_config.logger.error(f"Failed to delete some completed copy tasks: {errors}")
        return False
    return True


def remove_file(directory, file_name):
    response = _request(
        "POST",
        "/api/fs/remove",
        json={"names": [file_name], "dir": directory},
    )
    text = parse_json_response(response)
    return bool(text and text.get("code") == 200)


def mkdir(path):
    response = _request("POST", "/api/fs/mkdir", json={"path": path})
    text = parse_json_response(response)
    if not text or text.get("code") != 200:
        message = text.get("message") if text else "invalid JSON response"
        logger_config.logger.error(
            f"Failed to create directory {path} at {config.alist_url}: {message}"
        )
        return False
    return True
