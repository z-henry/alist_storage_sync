import time
import logger_config  # 导入日志配置
from threading import Thread
from api_client import task_undone
from sync import perform_sync
from config import sync_tasks

CHECK_INTERVAL = 60  # 检查未完成任务的间隔时间（秒）
SYNC_INTERVAL = 3600  # 同步任务的间隔时间（秒）

def check_tasks():
    while True:
        try:
            tasks = task_undone()
            if tasks:
                logger_config.logger.info("Tasks found, waiting for 1 minute...")
                time.sleep(CHECK_INTERVAL)
            else:
                logger_config.logger.info("No tasks found, performing sync...")
                for sync_task in sync_tasks:
                    perform_sync(sync_task)
                logger_config.logger.info("Sync end...")
                time.sleep(SYNC_INTERVAL)
        except Exception as e:
            logger_config.logger.error(f"An error occurred: {e}")
            time.sleep(SYNC_INTERVAL)

def start_task_checker():
    task_thread = Thread(target=check_tasks)
    task_thread.daemon = True
    task_thread.start()
