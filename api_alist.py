import requests
import json
import logger_config  # 导入日志配置
from config import authorization, alist_url

headers = {
    'Authorization': authorization,
    'Content-Type': 'application/json'
}

def parse_json_response(response):
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as e:
        logger_config.logger.error(f"Failed to parse JSON response: {e}")
        return None

def copy_done():
    response = requests.get(
        f"{alist_url}/api/task/copy/done",
        headers=headers)
    text = parse_json_response(response)
    if text and text.get("code") == 200:
        return text.get("data", [])
    else:
        message = text.get("message") if text else "invalid JSON response"
        logger_config.logger.error(f"Failed to get done copy from {alist_url}: {message}")
        return None
    
def copy_undone():
    response = requests.get(
        f"{alist_url}/api/task/copy/undone",
        headers=headers)
    text = parse_json_response(response)
    if text and text.get("code") == 200:
        return text.get("data", [])
    else:
        message = text.get("message") if text else "invalid JSON response"
        logger_config.logger.error(f"Failed to get undone copy from {alist_url}: {message}")
        return None
    
def list_files(path, refresh=False):
    response = requests.post(
        f"{alist_url}/api/fs/list", 
        json={"path": path, "refresh": refresh},
        headers=headers)
    text = parse_json_response(response)
    if text['code'] == 200:
        return text.get("data", {}).get("content", []) or []
    else:
        logger_config.logger.error(f"Failed to list files from {alist_url} at {path}: {text['message']}")
        return None
    
def get_files(path, refresh=False):
    response = requests.post(
        f"{alist_url}/api/fs/get", 
        json={"path": path, "refresh": refresh},
        headers=headers)
    text = parse_json_response(response)
    if text['code'] == 200:
        return text.get("data", {})
    else:
        logger_config.logger.error(f"Failed to get files from {alist_url} at {path}: {text['message']}")
        return None

def copy_file(src_dir, dst_dir, file_name):
    response = requests.post(
        f"{alist_url}/api/fs/copy", 
        json={"src_dir": src_dir, "dst_dir": dst_dir, "names": [file_name]},
        headers=headers)
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

    # New AList/OpenList returns the created asynchronous tasks here. A missing
    # tasks field means the copy completed synchronously.
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
    response = requests.post(
        f"{alist_url}/api/task/copy/delete_some",
        json=task_ids,
        headers=headers,
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

def remove_file(dir, file_name):
    response = requests.post(
        f"{alist_url}/api/fs/remove", 
        json={"names": [file_name], "dir": dir},
        headers=headers)
    text = parse_json_response(response)
    return bool(text and text.get("code") == 200)

def mkdir(path):
    response = requests.post(
        f"{alist_url}/api/fs/mkdir",
        json={"path": path},
        headers=headers)
    text = parse_json_response(response)
    if not text or text.get("code") != 200:
        message = text.get("message") if text else f"HTTP {response.status_code}"
        logger_config.logger.error(
            f"Failed to create directory {path} at {alist_url}: {message}"
        )
        return False
    return True
