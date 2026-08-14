from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import queue
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import logger_config
import runtime_store
from api_alist import copy_done, copy_undone
from cashe_refresh import perform_cache_refresh, recursive_refresh_cache_all
from config import dir_tree_build_tasks, sync_tasks
from sync import perform_sync


@dataclass
class RunOutcome:
    status: str
    result: dict


@dataclass
class QueueItem:
    run_id: str
    func: object
    args: list


task_queue = queue.Queue()
scheduler = None
worker_thread = None
started_at = None


def task_worker():
    while True:
        item = task_queue.get()
        runtime_store.update_run(item.run_id, "running")
        try:
            outcome = item.func(*item.args, run_id=item.run_id)
            if isinstance(outcome, RunOutcome):
                runtime_store.update_run(item.run_id, outcome.status, result=outcome.result)
                if outcome.status == "waiting_alist":
                    runtime_store.finalize_waiting_alist_runs(item.run_id)
            else:
                runtime_store.update_run(item.run_id, "succeeded", result=outcome)
        except Exception as error:
            logger_config.logger.exception(f"[task worker] 任务执行时发生错误：{error}")
            runtime_store.update_run(item.run_id, "failed", error=error)
        finally:
            task_queue.task_done()


def start_worker():
    global worker_thread
    if worker_thread and worker_thread.is_alive():
        return
    worker_thread = threading.Thread(target=task_worker, daemon=True, name="task-worker")
    worker_thread.start()


def _enqueue(task_uuid, task_type, trigger_type, parameters, func, args, request_id=None):
    run_id = runtime_store.create_run(
        task_uuid=task_uuid,
        task_type=task_type,
        trigger_type=trigger_type,
        parameters=parameters,
        request_id=request_id,
    )
    task_queue.put(QueueItem(run_id=run_id, func=func, args=args))
    logger_config.logger.info(
        f"[task queue] 添加任务到队列：run_id={run_id}, type={task_type}, task={task_uuid}"
    )
    return run_id


def infer_dst_path(path) -> str:
    for task in sync_tasks:
        if task.src in path:
            return path.replace(task.src, task.dst, 1)
    return ""


def check_tasks(sync_task, refresh, trigger_type="scheduled", request_id=None):
    return _enqueue(
        task_uuid=sync_task.uuid,
        task_type="sync",
        trigger_type=trigger_type,
        parameters={
            "src": sync_task.src,
            "dst": sync_task.dst,
            "refresh": refresh,
        },
        func=execute_check_tasks,
        args=[sync_task, refresh],
        request_id=request_id,
    )


def execute_check_tasks(sync_task, refresh, run_id=None):
    logger_config.logger.info(f"[sync check] task:{sync_task.uuid} start")
    tasks = copy_undone()
    if tasks is None:
        raise RuntimeError("Failed to query AList copy tasks")
    if tasks:
        logger_config.logger.info("[sync check] Undone tasks found, skipping this run.")
        return RunOutcome(
            status="skipped_busy",
            result={
                "reason": "alist_copy_tasks_in_progress",
                "undone_count": len(tasks),
            },
        )

    logger_config.logger.info("[sync check] No undone tasks found, performing sync...")
    result = perform_sync(sync_task, refresh, run_id=run_id)
    if result.get("failures"):
        status = "failed"
    elif result.get("alist_tasks_created"):
        status = "waiting_alist"
    else:
        status = "succeeded"
    logger_config.logger.info(f"[sync check] task:{sync_task.uuid} {status}")
    return RunOutcome(status=status, result=result)


def check_cache_refresh(trigger_type="scheduled", request_id=None):
    if task_queue.qsize() > 1:
        logger_config.logger.info("[cache check] 任务队列中已有多个任务，跳过缓存刷新任务的添加。")
        run_id = runtime_store.create_run(
            task_uuid="cache-refresh",
            task_type="cache_refresh",
            trigger_type=trigger_type,
            parameters={},
            request_id=request_id,
        )
        runtime_store.update_run(
            run_id,
            "skipped_busy",
            result={"reason": "local_queue_busy", "queue_size": task_queue.qsize()},
        )
        return run_id

    return _enqueue(
        task_uuid="cache-refresh",
        task_type="cache_refresh",
        trigger_type=trigger_type,
        parameters={},
        func=execute_check_cache_refresh,
        args=[],
        request_id=request_id,
    )


def execute_check_cache_refresh(run_id=None):
    undone_tasks = copy_undone()
    done_tasks = copy_done()
    if undone_tasks is None or done_tasks is None:
        raise RuntimeError("Failed to query AList copy tasks")

    runtime_store.reconcile_alist_copy_tasks(done_tasks + undone_tasks)

    if undone_tasks:
        logger_config.logger.info("[cache check] Undone tasks found, skipping refresh.")
        return RunOutcome(
            status="skipped_busy",
            result={
                "reason": "alist_copy_tasks_in_progress",
                "undone_count": len(undone_tasks),
            },
        )

    succeeded_tasks = [task for task in done_tasks if task.get("state") == 2]
    failed_tasks = [task for task in done_tasks if task.get("state") != 2]

    result = {
        "success": not failed_tasks,
        "done_count": len(done_tasks),
        "succeeded_count": len(succeeded_tasks),
        "failed_count": len(failed_tasks),
        "failed_tasks": [
            {
                "name": task.get("name"),
                "state": task.get("state"),
                "status": task.get("status"),
            }
            for task in failed_tasks[:50]
        ],
        "refreshed_paths": [],
        "callbacks": [],
    }
    if succeeded_tasks:
        cache_result = perform_cache_refresh(succeeded_tasks, run_id=run_id)
        result.update(cache_result)
        result["success"] = not failed_tasks and cache_result.get("success", True)
    if result["success"]:
        return result
    return RunOutcome(status="failed", result=result)


def check_dir_tree_build(task, trigger_type="scheduled", request_id=None):
    return _enqueue(
        task_uuid=task.uuid,
        task_type="dir_tree_build",
        trigger_type=trigger_type,
        parameters={
            "src": task.src,
            "cron": task.cron,
            "qps": task.qps,
            "run_at_start": task.run_at_start,
        },
        func=execute_dir_tree_build,
        args=[task],
        request_id=request_id,
    )


def execute_dir_tree_build(task, run_id=None):
    logger_config.logger.info(f"[dir_tree_build check] task:{task.uuid} start")
    if task.qps <= 0:
        raise ValueError(f"Dir tree build task {task.uuid} qps must be greater than zero")
    count = recursive_refresh_cache_all(task.src, 1 / task.qps)
    logger_config.logger.info(
        f"[dir_tree_build check] task:{task.uuid} end. update {count} records..."
    )
    return {"refreshed_count": count, "src": task.src}


def start_checker():
    global scheduler, started_at
    if scheduler and scheduler.running:
        return

    start_worker()
    scheduler = BackgroundScheduler()
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    for sync_task in sync_tasks:
        scheduler.add_job(
            check_tasks,
            args=[sync_task, False],
            trigger=CronTrigger.from_crontab(sync_task.cron),
            id=f"sync:{sync_task.uuid}",
            name=f"同步任务 {sync_task.uuid}",
        )

    scheduler.add_job(
        check_cache_refresh,
        trigger=CronTrigger(minute="*"),
        id="system:cache-refresh",
        name="复制完成检查与缓存刷新",
    )

    for task in dir_tree_build_tasks:
        kwargs = {}
        if task.run_at_start:
            kwargs["next_run_time"] = datetime.now() + timedelta(minutes=2)
        scheduler.add_job(
            check_dir_tree_build,
            args=[task],
            trigger=CronTrigger.from_crontab(task.cron),
            id=f"dir-tree:{task.uuid}",
            name=f"目录树刷新 {task.uuid}",
            **kwargs,
        )

    scheduler.start()
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def scheduler_jobs():
    if not scheduler:
        return []
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": (
                    job.next_run_time.astimezone(timezone.utc).isoformat(timespec="milliseconds")
                    if job.next_run_time
                    else None
                ),
                "trigger": str(job.trigger),
            }
        )
    return jobs


def runtime_snapshot():
    return {
        "started_at": started_at,
        "queue_size": task_queue.qsize(),
        "unfinished_queue_items": task_queue.unfinished_tasks,
        "worker_alive": bool(worker_thread and worker_thread.is_alive()),
        "scheduler_running": bool(scheduler and scheduler.running),
        "scheduler_jobs": scheduler_jobs(),
    }
