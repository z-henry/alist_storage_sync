from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import logger_config  # 导入日志配置
import logging
import threading
import queue
from datetime import datetime

from api_alist import copy_undone, copy_done
from sync import perform_sync
from cashe_refresh import perform_cache_refresh, recursive_refresh_cache_all
from config import sync_tasks, dir_tree_build_tasks

# 创建一个全局任务队列
task_queue = queue.Queue()

# 工作线程函数
def task_worker():
    while True:
        func, args = task_queue.get()
        try:
            func(*args)
        except Exception as e:
            logger_config.logger.error(f"[task worker] 任务执行时发生错误：{e}")
        finally:
            task_queue.task_done()

# 启动工作线程
def start_worker():
    worker_thread = threading.Thread(target=task_worker, daemon=True)
    worker_thread.start()
    
# 遍历 sync_tasks 列表，检查是否有任务的 src 出现在 path 中
def infer_dst_path(path) -> str:    
    for task in sync_tasks:
        if task.src in path:
            # 如果 src 是 path 的子串，则将 src 替换为 dst
            return path.replace(task.src, task.dst)
    # 如果没有匹配到，返回原始 path
    return ''

# 修改后的 check_tasks 函数
def check_tasks(sync_task, refresh):
    # 将任务添加到队列
    task_queue.put((execute_check_tasks, [sync_task, refresh]))
    logger_config.logger.info(f"[task queue] 添加同步任务到队列：{sync_task}")
    
    
def execute_check_tasks(sync_task, refresh):
    logger_config.logger.info(f"[sync check] task:{sync_task.uuid} start")
    try:
        tasks = copy_undone()
        if tasks:
            logger_config.logger.info("[sync check] Undone tasks found, waiting...")
        else:
            logger_config.logger.info("[sync check] No undone tasks found, performing sync...")
            perform_sync(sync_task, refresh)
            logger_config.logger.info(f"[sync check] task:{sync_task.uuid} end...")
    except Exception as e:
        logger_config.logger.error(f"[sync check] An error occurred: {e}")
            
def check_cache_refresh():
    # 如果任务队列的大小大于1，则不添加任务
    if task_queue.qsize() > 1:
        logger_config.logger.info("[cache check] 任务队列中已有多个任务，跳过缓存刷新任务的添加。")
        return
    # 将任务添加到队列
    task_queue.put((execute_check_cache_refresh, []))
    logger_config.logger.info("[task queue] 添加缓存刷新任务到队列。")
    

def execute_check_cache_refresh():
    try:
        tasks = copy_undone()
        if tasks:
            logger_config.logger.info("[cache check] Undone tasks found, waiting...")
        else:
            logger_config.logger.debug("[cache check] No undone tasks found, performing cache refresh...")
            
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
            logger_config.logger.debug("[cache check] Sync end...")
    except Exception as e:
        logger_config.logger.error(f"[cache check] An error occurred: {e}")
        
def check_dir_tree_build(task):
    # 将任务添加到队列
    task_queue.put((execute_dir_tree_build, [task]))
    logger_config.logger.info(f"[task queue] 添加目录树重建到队列：{task}")
    
    
def execute_dir_tree_build(task):
    logger_config.logger.info(f"[dir_tree_build check] task:{task.uuid} start")
    try:
            logger_config.logger.info("[dir_tree_build check] performing...")
            n_count= recursive_refresh_cache_all(task.src, 1/task.qps)
            logger_config.logger.info(f"[dir_tree_build check] task:{task.uuid} end. update {n_count} records...")
    except Exception as e:
        logger_config.logger.error(f"[dir_tree_build check] An error occurred: {e}")

def start_checker():
    start_worker()  # 启动任务工作线程
    
    scheduler = BackgroundScheduler()
    logging.getLogger('apscheduler').setLevel(logging.WARNING)

    for sync_task in sync_tasks:
        scheduler.add_job(check_tasks, args=[sync_task, False], trigger=CronTrigger.from_crontab(sync_task.cron))
    scheduler.add_job(check_cache_refresh, trigger=CronTrigger(minute="*"))
    for dir_tree_build_task in dir_tree_build_tasks:
        scheduler.add_job(check_dir_tree_build, args=[dir_tree_build_task], trigger=CronTrigger.from_crontab(dir_tree_build_task.cron), next_run_time=datetime.now())

    scheduler.start()

