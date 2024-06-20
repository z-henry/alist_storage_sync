import time
import logger_config  # 导入日志配置
from threading import Thread
from api_alist import copy_undone, copy_done, copy_clear_succeeded
from sync import perform_sync
from cashe_refresh import perform_cache_refresh
from config import sync_tasks


def check_tasks():
    CHECK_INTERVAL = 60  # 检查未完成任务的间隔时间（秒）
    SYNC_INTERVAL = 3600  # 同步任务的间隔时间（秒）
    while True:
        try:
            tasks = copy_undone()
            if tasks:
                logger_config.logger.info("[sync check]Undone tasks found, waiting...")
                time.sleep(CHECK_INTERVAL)
            else:
                logger_config.logger.info("[sync check]No undone tasks found, performing sync...")
                for sync_task in sync_tasks:
                    perform_sync(sync_task)
                logger_config.logger.info("[sync check]Sync end...")
                time.sleep(SYNC_INTERVAL)
        except Exception as e:
            logger_config.logger.error(f"[sync check]An error occurred: {e}")
            time.sleep(SYNC_INTERVAL)
            

def check_cache_refresh():
    INTERVAL = 60  # 间隔时间（秒）
    while True:
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
                perform_cache_refresh(succeeded_tasks)
                copy_clear_succeeded()
                logger_config.logger.debug("[cache check]Sync end...")
            time.sleep(INTERVAL)
        except Exception as e:
            logger_config.logger.error(f"[cache check]An error occurred: {e}")
            time.sleep(INTERVAL)

def start_task_checker():
    task_thread = Thread(target=check_tasks)
    task_thread.daemon = True
    task_thread.start()
    
def start_cache_refresh_checker():
    task_thread = Thread(target=check_cache_refresh)
    task_thread.daemon = True
    task_thread.start()

