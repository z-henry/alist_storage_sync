import re
from api_emby import media_update
from api_alist import get_files
from config import emby_enbale, emby_mount_path
import logger_config  # 导入日志配置

def get_path(tasks):
    # 筛选出 status 为 "getting src object" 的项目
    filtered_data = [task for task in tasks if task['status'] == 'getting src object']
    
    # 正则表达式模式
    pattern = r'^copy \[(.*)\]\((.*)\)\sto\s\[(.*)\]\((.*)\)$'
    
    # 提取路径并去重
    paths = set()
    path_mapping = {}
    for item in filtered_data:
        match = re.match(pattern, item['name'])
        if match:
            path1 = match.group(3)
            path2 = match.group(4)
            key = path1 + path2
            paths.add(key)
            # 提取$2中最后一个/之后的字符串
            source_path = match.group(2)
            last_part = source_path.split('/')[-1]
            path_mapping[key] = last_part

    # 转换为列表
    return list(paths), [emby_mount_path + k + "/" + v for k, v in path_mapping.items()]

def perform_cache_refresh(tasks):
    alist_unique_paths, emby_unique_files  = get_path(tasks)
    
    for path in alist_unique_paths:
        if not get_files(path, True):
            logger_config.logger.error(f"Failed to update alist cache at {path}")
            return
        logger_config.logger.info(f"Succeed to update alist cache at {path}")
        
        
    if emby_enbale:
        if not media_update(emby_unique_files):
            for file in emby_unique_files:
                logger_config.logger.error(f"Failed to update emby at {file}")
            return
        else:
            for file in emby_unique_files:
                logger_config.logger.info(f"Succeed to update emby at {file}")