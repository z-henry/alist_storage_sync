from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logger_config  # 导入日志配置
import logging

from api_alist import copy_undone, copy_done
from sync import perform_sync
from cashe_refresh import perform_cache_refresh
from config import sync_tasks

# 遍历 sync_tasks 列表，检查是否有任务的 src 出现在 path 中
def infer_dst_path(path) -> str:    
    for task in sync_tasks:
        if task.src in path:
            # 如果 src 是 path 的子串，则将 src 替换为 dst
            return path.replace(task.src, task.dst)
    # 如果没有匹配到，返回原始 path
    return ''


def check_tasks(sync_task):
    try:
        tasks = copy_undone()
        if tasks:
            logger_config.logger.info("[sync check]Undone tasks found, waiting...")
        else:
            logger_config.logger.info("[sync check]No undone tasks found, performing sync...")
            perform_sync(sync_task)
            logger_config.logger.info("[sync check]Sync end...")
    except Exception as e:
        logger_config.logger.error(f"[sync check]An error occurred: {e}")
            

def check_cache_refresh():
    try:
        tasks = copy_undone()
        if tasks:
            logger_config.logger.info("[cache check]Undone tasks found, waiting...")
        else:
            logger_config.logger.debug("[cache check]No undone tasks found, performing cache refresh...")
            
            # Classify done tasks by state
            done_tasks = copy_done()
            succeeded_tasks = []
            failed_tasks = []
            for done_task in done_tasks:
                if done_task["state"] == 2:
                    succeeded_tasks.append(done_task)
                else:
                    failed_tasks.append(done_task)
                    
            # For succeeded tasks, perform cache refresh
            if succeeded_tasks:
                perform_cache_refresh(succeeded_tasks)
            logger_config.logger.debug("[cache check]Sync end...")
    except Exception as e:
        logger_config.logger.error(f"[cache check]An error occurred: {e}")

def start_checker():
    scheduler = BackgroundScheduler()
    logging.getLogger('apscheduler').setLevel(logging.WARNING)

    for sync_task in sync_tasks:
        scheduler.add_job(check_tasks, args=[sync_task], trigger=CronTrigger.from_crontab(sync_task.cron))
    scheduler.add_job(check_cache_refresh, trigger=CronTrigger(minute="*"))

    scheduler.start()

