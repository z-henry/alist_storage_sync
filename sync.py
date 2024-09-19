import os
import logger_config  # 导入日志配置
from api_alist import list_files, get_files, copy_file, remove_file, mkdir
from config import cover_dst_when_diff, delete_src_when_same, Task
from cashe_refresh import recursive_refresh_cache

def sync_files(path_src, path_dst):
    files_src = list_files(path_src)
    if files_src is None: 
        return
    files_dst = list_files(path_dst)
    
    # Check if the destination directory exists, create it if it doesn't
    if not files_dst:
        parent_dir_dst, last_part_dst = os.path.split(path_dst.rstrip('/'))
        parent_dir_src, last_part_src = os.path.split(path_src.rstrip('/'))
        mkdir(parent_dir_dst)
        logger_config.logger.info(f"Creating directory {parent_dir_dst}")
        if not copy_file(parent_dir_src, parent_dir_dst, last_part_src):
            logger_config.logger.error(f"Failed to copy {last_part_src} from {parent_dir_src} to {parent_dir_dst}")
        logger_config.logger.info(f"Copy new directory {parent_dir_dst}")
        return
    
    files_dst_dict = {file["name"]: file for file in files_dst}
    
    for file_src in files_src:
        file_name = file_src["name"]
        file_size = file_src["size"]
        file_src_path = os.path.join(path_src, file_name)
        file_dst_path = os.path.join(path_dst, file_name)
        
        file_dst_info = files_dst_dict.get(file_name)
        
        # If file/floder does not exist in destination, copy it
        if file_dst_info is None:
            logger_config.logger.info(f"Copying new file {file_name} from {path_src} to {path_dst}")
            if not copy_file(path_src, path_dst, file_name):
                logger_config.logger.error(f"Failed to copy {file_name} from {path_src} to {path_dst}")
        # If file/folder exists in destination, check if it is a folder or a file
        # If it is a folder, recursively sync the folder
        elif file_src["is_dir"] == True:
            sync_files(file_src_path, file_dst_path)
        # If it is a file, check if the size is different
        else:
            # If the size is different, replace the file
            if file_size != file_dst_info["size"]:
                if cover_dst_when_diff:
                    logger_config.logger.info(f"Replacing file {file_name} in {path_dst} with new version from {path_src}")
                    if not remove_file(path_dst, file_name):
                        logger_config.logger.error(f"Failed to remove {file_name} from {path_dst}")
                    if not copy_file(path_src, path_dst, file_name):
                        logger_config.logger.error(f"Failed to copy {file_name} from {path_src} to {path_dst}")
            # If the size is the same, delete the source file
            else:
                if delete_src_when_same:
                    logger_config.logger.info(f"Deleting file {file_name} from {path_src}")
                    if not remove_file(path_src, file_name):
                        logger_config.logger.error(f"Failed to delete {file_name} from {path_src}")

def perform_sync(task, refresh = False):
    
    if refresh:
        if not recursive_refresh_cache(task.src):
            logger_config.logger.error(f"Failed to update alist cache at {task.src}")
            return
        
    if not get_files(task.src):
        logger_config.logger.info(f"Sync task src {task.src} no found")
        return
    
    if get_files(task.src)['is_dir'] == False:
        task_src_adjusted, last_part_src = os.path.split(task.src.rstrip('/'))
        logger_config.logger.info(f'Sync task.src "{task.src}" is not a directory, change to "{task_src_adjusted}"')
        task.src = task_src_adjusted
        task_dst_adjusted, last_part_dst = os.path.split(task.dst.rstrip('/'))
        logger_config.logger.info(f'Sync task.dst "{task.dst}" is not a directory, change to "{task_dst_adjusted}"')
        task.dst = task_dst_adjusted
    logger_config.logger.info(f'Sync task from "{task.src}" to "{task.dst}"')
    sync_files(task.src, task.dst)
