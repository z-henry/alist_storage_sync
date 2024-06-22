# alist_storage_sync
基于alist的不同存储间，自动同步复制工具

dockerhub： henryzzzzzz/alist_storage_sync


# 配置文件注解文档
## 示例

以下是一个完整的`config.json`示例，请配置在程序根目录下

```json
{
    "tasks": [
        {
            "src": "your_src_path",
            "dst": "your_dst_path"
        }
    ],
    "alist": {
        "url": "you_alist_url",
        "apikey": "your_alist_apikey"
    },
    "cover_dst_when_diff": true,
    "delete_src_when_same": true,
    "emby":{
        "enabled": true,
        "url": "you_emby_url",
        "apikey": "your_emby_apikey",
        "mount_path": "your_webdav_mount_base_path"
    }
}
```

## 字段说明

### tasks

`tasks`字段是一个任务列表，每个任务包含源路径和目标路径。

```json
"tasks": [
    {
        "src": "your_src_path",
        "dst": "your_dst_path"
    }
]
```

- `src`: alist中的源路径，例如 `/aliyun/emby`。
- `dst`: alist中的目标路径，例如 `/115/emby`。

### alist

`alist`字段包含有关alist服务的配置。

```json
"alist": {
    "url": "you_alist_url",
    "apikey": "your_alist_apikey"
}
```

- `url`: alist服务的URL，例如 `http://127.0.0.1:5244`。
- `apikey`: alist服务的API密钥，例如 `13212312312313232`。

### cover_dst_when_diff

`cover_dst_when_diff`字段是一个布尔值，用于决定当目标文件与源文件名称匹配，但是大小不同时，是否覆盖目标文件。

```json
"cover_dst_when_diff": true
```

- `true`: 覆盖目标文件。
- `false`: 不覆盖目标文件。

### delete_src_when_same

`delete_src_when_same`字段是一个布尔值，用于决定当目标文件与源文件名称匹配，且大小不同时，是否删除源文件。

```json
"delete_src_when_same": true
```

- `true`: 删除源文件。
- `false`: 不删除源文件。

### emby（试验性）
***webdav挂载必须挂载的事alist根目录***
***此操作会执行操作alist->任务->复制->清除已成功，此操作不可回溯，介意请勿启用***

`emby`字段包含有关emby服务的配置。

```json
"emby": {
    "enabled": true,
    "url": "you_emby_url",
    "apikey": "your_emby_apikey",
    "mount_path": "your_webdav_mount_base_path"
}
```

- `enabled`: 在alist复制完成时，是否通知emby进行刷新媒体库操作。
  - `true`: 启用emby刷新。
  - `false`: 不启用emby刷新。
- `url`: emby服务的URL，例如 `http://127.0.0.1:8096`。
- `apikey`: emby服务的API密钥，例如 `123123123123123`。
- `mount_path`: 将alist通过WebDAV挂载的本地路径，例如`/media/webdav`。

