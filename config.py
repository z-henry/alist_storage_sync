import json
import os

# 任务类
class Task:
    def __init__(self, src, dst):
        self.src = src
        self.dst = dst

# 加载配置文件
config_path = os.path.join(os.path.dirname(__file__), 'config.json')
with open(config_path, 'r') as f:
    config = json.load(f)

# 提供配置访问接口
authorization = config['alist']['apikey']
alist_url = config['alist']['url']
sync_tasks = [Task(task['src'], task['dst']) for task in config['tasks']]

#文件不同时覆盖目标文件
cover_dst_when_diff = config['cover_dst_when_diff']

#文件相同时删除源文件
delete_src_when_same = config['delete_src_when_same']

# emby 配置
emby_url = config['emby']['url']
emby_apikey = config['emby']['apikey']
emby_enbale = config['emby']['enabled']
emby_mount_path = config['emby']['mount_path']

