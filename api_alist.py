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

def copy_clear_succeeded():
    response = requests.post(
        f"{alist_url}/api/admin/task/copy/clear_succeeded", 
        headers=headers)
    text = parse_json_response(response)
    return text['code'] == 200
    
def copy_done():
    response = requests.get(
        f"{alist_url}/api/admin/task/copy/done", 
        headers=headers)
    text = parse_json_response(response)
    if text['code'] == 200:
        return text.get("data", [])
    else:
        logger_config.logger.error(f"Failed to get done copy from {alist_url}: {text['message']}")
        return None
    
def copy_undone():
    response = requests.get(
        f"{alist_url}/api/admin/task/copy/undone", 
        headers=headers)
    text = parse_json_response(response)
    if text['code'] == 200:
        return text.get("data", [])
    else:
        logger_config.logger.error(f"Failed to get undone copy from {alist_url}: {text['message']}")
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
    return text['code'] == 200

def remove_file(dir, file_name):
    response = requests.post(
        f"{alist_url}/api/fs/remove", 
        json={"names": [file_name], "dir": dir},
        headers=headers)
    text = parse_json_response(response)
    return text['code'] == 200

def mkdir(path):
    response = requests.post(
        f"{alist_url}/api/fs/mkdir",
        json={"path": path},
        headers=headers)
    if response.status_code != 200:
        logger_config.logger.error(f"Failed to create directory {path} at {alist_url}: {text['message']}")
    text = parse_json_response(response)
    return text['code'] == 200
