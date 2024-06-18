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
cover_dst_when_diff = config['cover_dst_when_diff']
delete_src_when_same = config['delete_src_when_same']
sync_tasks = [Task(task['src'], task['dst']) for task in config['tasks']]

