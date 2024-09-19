import json
import os

# 任务类
class Task:
    def __init__(self, uuid, src, dst, cron='1 * * * *', mounted_path = ""):
        self.src = src
        self.dst = dst
        self.cron = cron
        self.uuid = uuid
        self.mounted_path = mounted_path

# 加载配置文件
config_path = os.path.join(os.path.dirname(__file__), 'config.json')
with open(config_path, 'r',encoding='utf8') as f:
    config = json.load(f)

# 提供配置访问接口
authorization = config['alist']['apikey']
alist_url = config['alist']['url']
sync_tasks = [
    Task(
        task.get('uuid', str(index + 1)),  # 如果没有 uuid，就用从 1 开始的 index
        task['src'],
        task['dst'],
        task.get('cron', '1 * * * *'),
        task.get('mounted_path', '')
    )
    for index, task in enumerate(config['tasks'])
]

#文件不同时覆盖目标文件
cover_dst_when_diff = config['cover_dst_when_diff']

#文件相同时删除源文件
delete_src_when_same = config['delete_src_when_same']

# emby 配置
emby_enable = config['emby']['enabled']
emby_url = config['emby']['url']
emby_apikey = config['emby']['apikey']
emby_mount_path = config['emby']['mount_path']

#webhook 配置
webhook_enable = config['webhook']['enabled']
webhook_url = config['webhook']['url']

