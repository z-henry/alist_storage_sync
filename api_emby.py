import requests
import json
import logger_config  # 导入日志配置
from config import emby_apikey, emby_url

headers = {
    "X-Emby-Token": emby_apikey,
    "Content-Type": "application/json"
}


def media_update(paths):
    response = requests.post(
        f"{emby_url}/emby/Library/Media/Updated", 
        json={"Updates": [{"Path": path} for path in paths]},
        headers=headers)
    return response.status_code == 200 or response.status_code == 204