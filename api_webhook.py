import requests
import json
import logger_config  # 导入日志配置
from config import webhook_enable, webhook_url

headers = {
    "Content-Type": "application/json"
}

def media_update(paths):
    response = requests.post(
        webhook_url, 
        json={"Updates": [{"Path": path} for path in paths]},
        headers=headers)
    return response.status_code == 200 or response.status_code == 204