import re
import api_emby
import api_alist
import api_webhook
from config import emby_enable, webhook_enable
import logger_config  # 导入日志配置

def get_path(tasks):
    # 筛选出 status 为 "getting src object" 的项目
    filtered_data = [task for task in tasks if task['status'] == 'getting src object']
    
    # 正则表达式模式
    pattern = r'^copy \[(.*)\]\((.*)\)\sto\s\[(.*)\]\((.*)\)$'
    
    # 提取路径并去重
    path_mapping = {}
    for item in filtered_data:
        match = re.match(pattern, item['name'])
        if match:
            path1 = match.group(3)
            path2 = match.group(4)
            key = path1 + path2
            # 提取$2中最后一个/之后的字符串
            source_path = match.group(2)
            last_part = source_path.split('/')[-1]
            path_mapping[key] = last_part

    # 转换为列表
    return [k + "/" + v for k, v in path_mapping.items()]

def recursive_refresh_cache(path) -> bool:
    # 递归获取文件列表,由深到浅来刷新文件夹缓存
    # 循环每次去掉最后一个斜杠及其之后的部分
    tmp_path= path
    refresh_succeed = False
    while tmp_path:
        # 找到最后一个斜杠的位置，截取到最后一个斜杠之前的部分
        last_slash_index = tmp_path.rfind('/')
        if last_slash_index == -1:
            break
        tmp_path = tmp_path[:last_slash_index]

        if api_alist.list_files(tmp_path, True):
            refresh_succeed = True
            break
    return refresh_succeed

def perform_cache_refresh(tasks):
    unique_paths = get_path(tasks)
    
    for path in unique_paths:
        if not recursive_refresh_cache(path):
            logger_config.logger.error(f"Failed to update alist cache at {path}")
            return
        logger_config.logger.info(f"Succeed to update alist cache at {path}")
        
    api_alist.copy_clear_succeeded()
        
        
    if emby_enable:
        if not api_emby.media_update(unique_paths):
            for file in unique_paths:
                logger_config.logger.error(f"Failed to update emby at {file}")
            return 
        else:
            for file in unique_paths:
                logger_config.logger.info(f"Succeed to update emby at {file}")
                
    if webhook_enable:
        if not api_webhook.media_update(unique_paths):
            for file in unique_paths:
                logger_config.logger.error(f"Failed to call webhook at {file}")
            return 
        else:
            for file in unique_paths:
                logger_config.logger.info(f"Succeed to call webhook at {file}")
                