from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import queue
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import logger_config
import runtime_store
import api_alist
import config
from cashe_refresh import (
    cleanup_tracked_tasks,
    perform_cache_refresh,
    recursive_refresh_cache_all,
)
from sync import perform_sync
from features.alist2strm.service import run_alist2strm


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
strm_task_queue = queue.Queue()
scheduler = None
worker_thread = None
strm_worker_thread = None
started_at = None
_scheduler_lock = threading.RLock()
HEALTH_JOB_ID = "system:alist-health"


def task_worker(work_queue):
    while True:
        item = work_queue.get()
        if not api_alist.is_online():
            runtime_store.update_run(
                item.run_id,
                "skipped_unavailable",
                result={"reason": "alist_unavailable", "health": api_alist.health_snapshot()},
            )
            work_queue.task_done()
            continue
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
            work_queue.task_done()


def start_worker():
    global worker_thread, strm_worker_thread
    if not worker_thread or not worker_thread.is_alive():
        worker_thread = threading.Thread(
            target=task_worker,
            args=[task_queue],
            daemon=True,
            name="task-worker",
        )
        worker_thread.start()
    if not strm_worker_thread or not strm_worker_thread.is_alive():
        strm_worker_thread = threading.Thread(
            target=task_worker,
            args=[strm_task_queue],
            daemon=True,
            name="alist2strm-worker",
        )
        strm_worker_thread.start()


def _enqueue(
    task_uuid,
    task_type,
    trigger_type,
    parameters,
    func,
    args,
    request_id=None,
    work_queue=None,
):
    if not api_alist.is_online():
        run_id = runtime_store.create_run(
            task_uuid=task_uuid,
            task_type=task_type,
            trigger_type=trigger_type,
            parameters=parameters,
            request_id=request_id,
        )
        runtime_store.update_run(
            run_id,
            "skipped_unavailable",
            result={"reason": "alist_unavailable", "health": api_alist.health_snapshot()},
        )
        logger_config.logger.warning(
            f"[task queue] AList 不可用，跳过任务：type={task_type}, task={task_uuid}"
        )
        return run_id

    run_id, active_run = runtime_store.create_run_if_instance_idle(
        task_uuid=task_uuid,
        task_type=task_type,
        trigger_type=trigger_type,
        parameters=parameters,
        request_id=request_id,
    )
    if active_run:
        logger_config.logger.info(
            "[task queue] 实例任务尚未完成，跳过本次触发："
            f"run_id={run_id}, type={task_type}, task={task_uuid}, "
            f"blocking_run_id={active_run['run_id']}, "
            f"blocking_status={active_run['status']}"
        )
        return run_id

    destination_queue = task_queue if work_queue is None else work_queue
    destination_queue.put(QueueItem(run_id=run_id, func=func, args=args))
    logger_config.logger.info(
        f"[task queue] 添加任务到队列：run_id={run_id}, type={task_type}, task={task_uuid}"
    )
    return run_id


def infer_dst_path(path) -> str:
    for task in config.sync_tasks:
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
    result = perform_sync(sync_task, refresh, run_id=run_id)
    if result.get("failures"):
        status = "failed"
    elif result.get("alist_tasks_created"):
        status = "waiting_alist"
    else:
        status = "succeeded"
    logger_config.logger.info(f"[sync check] task:{sync_task.uuid} {status}")
    return RunOutcome(status=status, result=result)


def check_alist2strm(
    task,
    trigger_type="scheduled",
    request_id=None,
    changed_paths=None,
    source_run_id=None,
):
    return _enqueue(
        task_uuid=task.uuid,
        task_type="alist2strm",
        trigger_type=trigger_type,
        parameters={
            **task.parameters(),
            "output_root": config.strm_output_root,
            "incremental": changed_paths is not None,
            "incremental_path_count": len(changed_paths or ()),
            "source_run_id": source_run_id,
        },
        func=execute_alist2strm,
        args=[
            task,
            config.alist_url,
            config.authorization,
            config.alist_request_timeout_seconds,
            config.strm_output_root,
            tuple(changed_paths) if changed_paths is not None else None,
        ],
        request_id=request_id,
        work_queue=strm_task_queue,
    )


def execute_alist2strm(
    task,
    alist_url,
    api_key,
    request_timeout,
    output_root,
    changed_paths,
    run_id=None,
):
    result = run_alist2strm(
        task=task,
        alist_url=alist_url,
        api_key=api_key,
        request_timeout=request_timeout,
        output_root=output_root,
        changed_paths=changed_paths,
    )
    return RunOutcome(
        status="failed" if result.get("failed") else "succeeded",
        result=result,
    )


def _enqueue_internal_alist2strm(detail, source_run_id):
    triggers = detail.get("alist2strm_triggers") or []
    if not triggers:
        return

    tasks_by_uuid = {task.uuid: task for task in config.alist2strm_tasks}
    runs = []
    errors = []
    for trigger in triggers:
        task_uuid = trigger.get("task_uuid")
        task = tasks_by_uuid.get(task_uuid)
        if task is None:
            errors.append({"task_uuid": task_uuid, "error": "task_not_found"})
            continue
        try:
            paths = trigger.get("paths") or []
            strm_run_id = check_alist2strm(
                task,
                trigger_type="postprocess",
                changed_paths=paths,
                source_run_id=source_run_id,
            )
            strm_run = runtime_store.get_run(strm_run_id, child_limit=1) or {}
            runs.append(
                {
                    "task_uuid": task_uuid,
                    "run_id": strm_run_id,
                    "status": strm_run.get("status"),
                    "path_count": len(paths),
                }
            )
        except Exception as error:
            logger_config.logger.exception(
                "[cache check] failed to enqueue internal Alist2Strm task=%s: %s",
                task_uuid,
                error,
            )
            errors.append({"task_uuid": task_uuid, "error": str(error)})

    detail["alist2strm_runs"] = runs
    if errors:
        detail["alist2strm_enqueue_errors"] = errors
        detail["success"] = False


def check_cache_refresh(trigger_type="scheduled", request_id=None):
    if not api_alist.is_online():
        return _enqueue(
            task_uuid="cache-refresh",
            task_type="cache_refresh",
            trigger_type=trigger_type,
            parameters={},
            func=execute_check_cache_refresh,
            args=[],
            request_id=request_id,
        )
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
    done_tasks = api_alist.copy_done()
    undone_tasks = api_alist.copy_undone()
    if undone_tasks is None or done_tasks is None:
        raise RuntimeError("Failed to query AList copy tasks")

    reconcile_result = runtime_store.reconcile_alist_copy_tasks(
        done_tasks=done_tasks,
        undone_tasks=undone_tasks,
        missing_timeout_seconds=config.alist_task_missing_timeout_seconds,
    )
    result = {
        "success": True,
        "alist_done_count": len(done_tasks),
        "alist_undone_count": len(undone_tasks),
        "tracked_done": reconcile_result["tracked_done"],
        "tracked_undone": reconcile_result["tracked_undone"],
        "missing_timed_out": reconcile_result["missing_timed_out"],
        "finalized_runs": reconcile_result["finalized_runs"],
        "postprocessed_runs": [],
    }

    for parent in runtime_store.claim_pending_postprocess_runs():
        parent_run_id = parent["run_id"]
        try:
            if parent["status"] == "succeeded":
                detail = perform_cache_refresh(
                    parent["tasks"],
                    run_id=parent_run_id,
                )
                if detail.get("success"):
                    _enqueue_internal_alist2strm(detail, parent_run_id)
            else:
                cleanup = cleanup_tracked_tasks(parent["tasks"])
                detail = {
                    "success": cleanup["success"],
                    "cleanup": cleanup,
                    "callbacks": [],
                    "refreshed_paths": [],
                    "failed_paths": [],
                    "alist2strm_triggers": [],
                }
        except Exception as error:
            logger_config.logger.exception(
                f"[cache check] 父任务后处理失败：run_id={parent_run_id}: {error}"
            )
            detail = {"success": False, "error": str(error)}

        runtime_store.finish_run_postprocess(
            parent_run_id,
            success=detail.get("success", False),
            result=detail,
        )
        result["postprocessed_runs"].append(
            {
                "run_id": parent_run_id,
                "parent_status": parent["status"],
                "success": detail.get("success", False),
            }
        )
        if not detail.get("success", False):
            result["success"] = False

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

    with _scheduler_lock:
        start_worker()
        scheduler = BackgroundScheduler()
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        _add_scheduler_jobs()
        scheduler.start()
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    refresh_alist_health()


def validate_scheduler_config(data):
    for task in data.get("tasks", []):
        CronTrigger.from_crontab(task.get("cron", "1 * * * *"))
    for task in data.get("alist2strm_tasks", []):
        CronTrigger.from_crontab(task.get("cron", "0 */6 * * *"))
    for task in data.get("dir_tree_build_tasks", []):
        CronTrigger.from_crontab(task["cron"])


def _add_scheduler_jobs():
    for sync_task in config.sync_tasks:
        scheduler.add_job(
            check_tasks,
            args=[sync_task, False],
            trigger=CronTrigger.from_crontab(sync_task.cron),
            id=f"sync:{sync_task.uuid}",
            name=f"同步任务 {sync_task.uuid}",
        )

    for task in config.alist2strm_tasks:
        scheduler.add_job(
            check_alist2strm,
            args=[task],
            trigger=CronTrigger.from_crontab(task.cron),
            id=f"alist2strm:{task.uuid}",
            name=f"STRM 生成 {task.uuid}",
        )
    scheduler.add_job(
        check_cache_refresh,
        trigger=CronTrigger(minute="*"),
        id="system:cache-refresh",
        name="子任务巡检与父任务后处理",
    )

    for task in config.dir_tree_build_tasks:
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

    scheduler.add_job(
        refresh_alist_health,
        trigger="interval",
        seconds=config.alist_healthcheck_interval_seconds,
        id=HEALTH_JOB_ID,
        name="AList 可用性检查",
        max_instances=1,
        coalesce=True,
    )


def _set_operational_jobs_paused(paused):
    if not scheduler or not scheduler.running:
        return
    with _scheduler_lock:
        for job in scheduler.get_jobs():
            if job.id == HEALTH_JOB_ID:
                continue
            if paused and job.next_run_time is not None:
                scheduler.pause_job(job.id)
            elif not paused and job.next_run_time is None:
                scheduler.resume_job(job.id)


def refresh_alist_health():
    previous = api_alist.health_snapshot()
    health = api_alist.check_health()
    _set_operational_jobs_paused(not health["online"])
    if previous.get("online") != health["online"]:
        level = logger_config.logger.info if health["online"] else logger_config.logger.warning
        level(
            "[alist health] "
            + ("AList 已恢复，业务调度已启用" if health["online"] else "AList 不可用，业务调度已暂停")
        )
    return health


def reload_scheduler():
    if not scheduler:
        return refresh_alist_health()
    with _scheduler_lock:
        for job in list(scheduler.get_jobs()):
            scheduler.remove_job(job.id)
        _add_scheduler_jobs()
    return refresh_alist_health()


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
    alist_health = api_alist.health_snapshot()
    return {
        "started_at": started_at,
        "queue_size": task_queue.qsize(),
        "unfinished_queue_items": task_queue.unfinished_tasks,
        "worker_alive": bool(worker_thread and worker_thread.is_alive()),
        "scheduler_running": bool(scheduler and scheduler.running),
        "scheduler_jobs": scheduler_jobs(),
        "strm_queue_size": strm_task_queue.qsize(),
        "unfinished_strm_queue_items": strm_task_queue.unfinished_tasks,
        "strm_worker_alive": bool(strm_worker_thread and strm_worker_thread.is_alive()),
        "alist": alist_health,
        "operational_jobs_paused": not alist_health["online"],
    }
