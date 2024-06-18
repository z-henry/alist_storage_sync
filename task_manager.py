import time
import logger_config  # 导入日志配置
from threading import Thread
from api_client import task_undone
from sync import perform_sync
from config import sync_tasks


def check_tasks():
    while True:
        tasks = task_undone()
        if tasks:
            logger_config.logger.info("Tasks found, waiting for 1 minute...")
            time.sleep(60)
        else:
            logger_config.logger.info("No asks found, performing sync...")
            for sync_task in sync_tasks:
                perform_sync(sync_task)
            logger_config.logger.info("Sync end...")
            time.sleep(3600)

def start_task_checker():
    task_thread = Thread(target=check_tasks)
    task_thread.daemon = True
    task_thread.start()
