import os

import logger_config
import runtime_store
import config
from api_alist import copy_file, get_files, list_files, mkdir, remove_file
from cashe_refresh import recursive_refresh_cache


def _new_summary():
    return {
        "scanned_entries": 0,
        "directories_created": 0,
        "copy_requests_submitted": 0,
        "copy_requests_completed_immediately": 0,
        "alist_tasks_created": 0,
        "replacement_requests_submitted": 0,
        "source_files_deleted": 0,
        "unchanged_entries": 0,
        "failures": 0,
    }


def _submit_copy(path_src, path_dst, file_name, summary, run_id):
    tasks = copy_file(path_src, path_dst, file_name)
    if tasks is None:
        return False

    summary["copy_requests_submitted"] += 1
    if tasks:
        summary["alist_tasks_created"] += len(tasks)
        runtime_store.record_alist_copy_tasks(
            run_id=run_id,
            tasks=tasks,
            source_dir=path_src,
            destination_dir=path_dst,
            entry_name=file_name,
        )
    else:
        summary["copy_requests_completed_immediately"] += 1
    return True


def sync_files(path_src, path_dst, summary=None, run_id=None):
    summary = summary or _new_summary()
    files_src = list_files(path_src)
    if files_src is None:
        summary["failures"] += 1
        return summary

    files_dst = list_files(path_dst)
    if files_dst is None:
        if not mkdir(path_dst):
            logger_config.logger.error(f"Failed to create directory {path_dst}")
            summary["failures"] += 1
            return summary
        summary["directories_created"] += 1
        logger_config.logger.info(f"Creating directory {path_dst}")
        return sync_files(path_src, path_dst, summary, run_id)

    files_dst_dict = {file["name"]: file for file in files_dst}

    for file_src in files_src:
        summary["scanned_entries"] += 1
        file_name = file_src["name"]
        file_size = file_src["size"]
        file_src_path = os.path.join(path_src, file_name)
        file_dst_path = os.path.join(path_dst, file_name)
        file_dst_info = files_dst_dict.get(file_name)

        if file_dst_info is None and file_src["is_dir"] is True:
            # Copying a directory makes OpenList create additional tasks later,
            # but those dynamic tasks do not expose a parent/group id. Create
            # directories here and submit leaf files individually so every copy
            # task can be associated with this run by its returned id.
            if not mkdir(file_dst_path):
                logger_config.logger.error(f"Failed to create directory {file_dst_path}")
                summary["failures"] += 1
                continue
            summary["directories_created"] += 1
            logger_config.logger.info(f"Creating directory {file_dst_path}")
            sync_files(file_src_path, file_dst_path, summary, run_id)
        elif file_dst_info is None:
            logger_config.logger.info(f"Copying new file {file_name} from {path_src} to {path_dst}")
            if not _submit_copy(path_src, path_dst, file_name, summary, run_id):
                logger_config.logger.error(f"Failed to copy {file_name} from {path_src} to {path_dst}")
                summary["failures"] += 1
        elif file_src["is_dir"] is True:
            sync_files(file_src_path, file_dst_path, summary, run_id)
        elif file_size != file_dst_info["size"]:
            if config.cover_dst_when_diff:
                logger_config.logger.info(
                    f"Replacing file {file_name} in {path_dst} with new version from {path_src}"
                )
                if not remove_file(path_dst, file_name):
                    logger_config.logger.error(f"Failed to remove {file_name} from {path_dst}")
                    summary["failures"] += 1
                    continue
                if _submit_copy(path_src, path_dst, file_name, summary, run_id):
                    summary["replacement_requests_submitted"] += 1
                else:
                    logger_config.logger.error(f"Failed to copy {file_name} from {path_src} to {path_dst}")
                    summary["failures"] += 1
            else:
                summary["unchanged_entries"] += 1
        elif config.delete_src_when_same:
            logger_config.logger.info(f"Deleting file {file_name} from {path_src}")
            if remove_file(path_src, file_name):
                summary["source_files_deleted"] += 1
            else:
                logger_config.logger.error(f"Failed to delete {file_name} from {path_src}")
                summary["failures"] += 1
        else:
            summary["unchanged_entries"] += 1

    return summary


def perform_sync(task, refresh=False, run_id=None):
    if refresh and not recursive_refresh_cache(task.src):
        raise RuntimeError(f"Failed to update alist cache at {task.src}")

    source_info = get_files(task.src)
    if not source_info:
        logger_config.logger.info(f"Sync task src {task.src} not found")
        result = _new_summary()
        result.update({"src": task.src, "dst": task.dst, "source_found": False})
        return result

    src = task.src
    dst = task.dst
    if source_info.get("is_dir") is False:
        src, _ = os.path.split(src.rstrip("/"))
        dst, _ = os.path.split(dst.rstrip("/"))
        logger_config.logger.info(f'Sync source is a file, using parent directory "{src}"')

    logger_config.logger.info(f'Sync task from "{src}" to "{dst}"')
    result = sync_files(src, dst, run_id=run_id)
    result.update({"src": src, "dst": dst, "source_found": True})
    return result
