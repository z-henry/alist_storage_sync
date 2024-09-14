from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import time
import logger_config  # 导入日志配置
import logging

from api_alist import copy_undone, copy_done
from sync import perform_sync
from cashe_refresh import perform_cache_refresh
from config import sync_tasks


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

