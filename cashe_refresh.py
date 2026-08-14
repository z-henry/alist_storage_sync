import os
import posixpath
import re
from time import sleep

import api_alist
import api_emby
import api_webhook
import logger_config
import runtime_store
from config import emby_enable, emby_url, webhook_enable, webhook_url


def get_path(tasks):
    pattern = r'^copy \[(.*)\]\((.*)\)\sto\s\[(.*)\]\((.*)\)$'

    paths = []
    for item in tasks:
        match = re.match(pattern, item.get("name", ""))
        if match:
            destination_mount = match.group(3)
            destination_path = match.group(4)
            source_name = posixpath.basename(match.group(2))
            paths.append(
                posixpath.join(
                    destination_mount,
                    destination_path.lstrip("/"),
                    source_name,
                )
            )
    return list(dict.fromkeys(paths))


def recursive_refresh_cache(path) -> bool:
    tmp_path = path
    while tmp_path:
        last_slash_index = tmp_path.rfind("/")
        if last_slash_index == -1:
            break
        tmp_path = tmp_path[:last_slash_index]
        files = api_alist.list_files(tmp_path, True)
        if files is not None:
            return True
    return False


def recursive_refresh_cache_all(path, delay) -> int:
    count = 1
    files_src = api_alist.list_files(path, True)
    logger_config.logger.info(f"refresh dir {path}")
    sleep(delay)

    if files_src is None:
        return count

    for file_src in files_src:
        if file_src["is_dir"] is True:
            count += recursive_refresh_cache_all(
                os.path.join(path, file_src["name"]), delay
            )
    return count


def _record_callback(service, target, detail, run_id):
    callback_id = runtime_store.record_callback(
        service=service,
        target=target,
        request_payload=detail.get("payload"),
        success=detail.get("success", False),
        response=detail.get("response"),
        status_code=detail.get("status_code"),
        duration_ms=detail.get("duration_ms"),
        error=detail.get("error"),
        run_id=run_id,
    )
    return {
        "callback_id": callback_id,
        "service": service,
        "success": detail.get("success", False),
        "status_code": detail.get("status_code"),
        "duration_ms": detail.get("duration_ms"),
        "error": detail.get("error"),
    }


def perform_cache_refresh(tasks, run_id=None):
    unique_paths = get_path(tasks)
    result = {
        "success": True,
        "refreshed_paths": [],
        "failed_paths": [],
        "callbacks": [],
        "cleared_succeeded_tasks": False,
    }

    for path in unique_paths:
        if recursive_refresh_cache(path):
            result["refreshed_paths"].append(path)
            logger_config.logger.info(f"Succeed to update alist cache at {path}")
        else:
            result["success"] = False
            result["failed_paths"].append(path)
            logger_config.logger.error(f"Failed to update alist cache at {path}")

    succeeded_task_ids = [
        task.get("id")
        for task in tasks
        if task.get("state") == 2 and task.get("id")
    ]
    result["cleared_succeeded_tasks"] = api_alist.copy_delete_tasks(succeeded_task_ids)
    if not result["cleared_succeeded_tasks"]:
        result["success"] = False

    if not unique_paths or result["failed_paths"]:
        return result

    if emby_enable:
        detail = api_emby.media_update_detail(unique_paths)
        callback = _record_callback("emby", emby_url, detail, run_id)
        result["callbacks"].append(callback)
        if not detail["success"]:
            result["success"] = False
            logger_config.logger.error("Failed to notify Emby")
            return result
        logger_config.logger.info("Succeed to notify Emby")

    if webhook_enable:
        detail = api_webhook.media_update_detail(unique_paths)
        callback = _record_callback("webhook", webhook_url, detail, run_id)
        result["callbacks"].append(callback)
        if not detail["success"]:
            result["success"] = False
            logger_config.logger.error("Failed to call webhook")
            return result
        logger_config.logger.info("Succeed to call webhook")

    return result
