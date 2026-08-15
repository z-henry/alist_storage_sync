# alist_storage_sync

![GitHub stars](https://img.shields.io/github/stars/z-henry/alist_storage_sync?style=social)
![GitHub release (latest by date)](https://img.shields.io/github/v/tag/z-henry/alist_storage_sync)
![Docker Pulls](https://img.shields.io/docker/pulls/henryzzzzzz/alist_storage_sync)
![Docker Image Size](https://img.shields.io/docker/image-size/henryzzzzzz/alist_storage_sync/latest)


`alist_storage_sync` 是一个用于定时同步两个 alist 存储的工具，并可以选择扫描已完成的复制任务，刷新缓存并通知 Emby 进行相应文件夹扫描。

## 功能

- **存储同步**: 通过 alist 复制的方式，定时将一个 alist 存储同步到另一个存储上。
- **驱动emby扫描**: 本系统创建的复制子任务全部成功后，刷新对应目标目录缓存并通知 Emby 扫描。
- **提供webhook**: 本系统创建的复制子任务全部成功后，在没有内部 STRM 任务匹配目标路径时触发外部下游流程。
- **alist目录树缓慢缓存**: 定时按照制定的qps，强制刷新指定的alist目录的目录树。
- **STRM 生成**: 定时扫描同一个 AList，将视频生成 `.strm` 文件，并可下载字幕、图片、NFO 和自定义扩展名文件。
*此功能用于应对115日益严重的风控，需要alist缓存时长设置超长，由此功能刷新缓存（建议qps<=0.5）。缺点是非`alist_storage_sync`同部任务更新到网盘更新的文件，不能及时刷新。依赖于目录树刷新任务的定时，或手动通过alist页面进行刷新*



## 配置

使用前，需要创建一个配置文件 `config.json`。详细的配置说明可以在 [Wiki](https://github.com/z-henry/alist_storage_sync/wiki/配置说明) 中找到。

### STRM 生成配置

STRM 任务通过 `alist2strm_tasks` 配置并复用 `alist.url` 与 `alist.apikey`，不需要额外填写账号密码。`target_dir` 必须是相对于 `STRM_OUTPUT_ROOT`（默认 `/media`）的子目录，不能使用绝对路径或 `..`。

`mode` 支持 `alist_url`（AList 签名直链）、`raw_url`（存储原始链接）和 `alist_path`（AList 文件路径）。平铺模式下字幕、图片和 NFO 下载会自动停用。完整字段参见 `config.json.example`，也可以在浏览器“配置管理”页面新增任务。

STRM 扫描使用独立任务队列，不会阻塞存储复制任务的巡检与后处理。同一 STRM 实例尚未完成时，后续定时或手动触发会记录为“忙碌跳过”。生成过程不会删除目标目录中已经存在但源端已不存在的文件。

多个源文件映射到同一个输出时，若目标文件已存在且 `overwrite=false`，所有候选都会直接跳过，不会重新生成；目标不存在或开启覆盖时，默认选择体积最大的源文件。运行详情会汇总同名输出组、处理动作和候选文件，方便后续清理源端重复文件。

同步复制成功并完成缓存刷新后，程序会按目标路径匹配 `alist2strm_tasks[].source_dir`，将文件或目录直接送入内部 STRM 队列增量处理，不再经过 HTTP API。路径匹配到内部 STRM 任务时会跳过旧 webhook，未匹配时 webhook 仍作为兼容兜底。

代码中的 Alist2Strm 设计源自 [AutoFilm](https://github.com/Akimio521/AutoFilm)，迁移时改为 API Key 认证、有界异步队列和原子文件写入。

## 部署

### 本机构建 Docker 镜像

项目根目录的 `build.sh` 会读取 `version.py` 并同时生成版本标签和 `latest` 标签：

```sh
./build.sh
# alist-storage-sync:1.16.0
# alist-storage-sync:latest
```

可以通过环境变量修改镜像名：

```sh
IMAGE_NAME=myrepo/alist-storage-sync ./build.sh
```

### Docker 部署

在使用 Docker 部署 `alist_storage_sync` 前，请确保已经创建好 `config.json` 并放置在合适的路径。控制台需要写入该文件才能在线保存配置，因此不要将它以 `:ro` 方式挂载，并确保容器对宿主文件有写权限。

1. **创建配置文件**

   创建 `config.json` 并放置在 `/your_path/` 路径下

2. **Docker Compose 配置**

   创建一个 `docker-compose.yml` 文件，并添加以下内容：

   ```yaml
   version: '3'
   services:
     alist_storage_sync:
       container_name: alist_storage_sync
       image: henryzzzzzz/alist_storage_sync:latest
       ports:
         - "8115:8115"
       volumes:
         - /your_path/config.json:/app/config.json
         - /your_path/log:/app/log
         - /your_path/data:/app/data
         - /your_path/media:/media
       environment:
         - TZ=Asia/Shanghai
         - UI_USERNAME=${UI_USERNAME:-admin}
         - STRM_OUTPUT_ROOT=/media
         - UI_PASSWORD=${UI_PASSWORD}
         - UI_SESSION_SECRET=${UI_SESSION_SECRET}
   ```

3. **运行 Docker 容器**

   在 `docker-compose.yml` 文件所在目录运行以下命令启动容器：

   ```sh
   docker-compose up -d
   ```

### 直接运行

如果不使用 Docker，也可以直接运行 `alist_storage_sync`。

1. **放置配置文件**

   确保 `config.json` 文件放置在项目根目录下。

   STRM 输出根目录默认为 `/media`；直接运行时可以通过 `STRM_OUTPUT_ROOT` 环境变量修改，并确保当前用户具有写权限。

2. **运行应用**

   使用以下命令运行应用：

   ```sh
   python app.py
   ```

## 浏览器任务控制台

应用启动后访问 `http://<服务器地址>:8115/ui`，可以查看定时任务、运行参数与结果、API 请求以及 Emby/Webhook 回调记录。

服务会按照 `alist.healthcheck_interval_seconds`（默认 15 秒）调用 OpenList 官方 `/ping`，并使用配置的 API Key 检查新版复制任务接口。AList 不可达、尚未就绪或 API Key 无效时，业务调度会暂停，任务 API 返回 HTTP 503；UI 和健康检查保持可用。检测恢复后业务调度自动继续。`alist.healthcheck_timeout_seconds` 控制单次探活超时，`alist.request_timeout_seconds` 控制其他 AList、Emby 和 Webhook 请求超时。

运行概览会按具体实例任务（例如同步、STRM 生成、`cache-refresh` 或目录树刷新实例）折叠父任务，并在实例标题展示最近执行时间；展开父任务后按需加载对应的 AList 文件子任务。运行记录页面平铺父任务，并支持按实例任务、时间范围和状态筛选。子任务每次加载 100 条，可继续分页加载。同一个实例任务存在排队中、运行中或等待 AList 子任务的父任务时，后续触发会记录为“忙碌跳过”，不会再次进入执行队列。

`cache-refresh` 实例实际承担“子任务巡检与父任务后处理”：`done` 用于确认本系统子任务的成功、失败或取消终态，`undone` 用于更新本系统子任务的运行进度。某个本地子任务同时不在 `done` 和 `undone` 中超过 `alist.task_missing_timeout_seconds`（默认 600 秒）后，才会按丢失超时处理。每个父任务独立判断；成功父任务刷新自己记录的目标路径，触发 Emby，并直接入队匹配的内部 STRM 任务；没有内部 STRM 任务匹配时再调用 Webhook。随后只清理该父任务创建的 AList task ID。未记录在本地数据库中的 AList 任务不会被更新、回调或删除。配置值设为 `0` 可以关闭丢失超时。

“配置管理”页面按 AList、同步任务、同步行为、STRM 生成、目录树刷新、Emby 和 Webhook 分模块设置，任务实例可以直接新增或删除，不需要手写 JSON。折叠的高级区域会展示最终 JSON 只读预览，原配置中的未知扩展字段会在表单保存时保留。保存时会校验必填字段、URL、Cron 和任务 UUID，写入成功后立即更新 AList/Emby/Webhook 参数并重建后续调度。已经排队或正在执行的任务继续使用入队时参数。配置中包含 API Key 等敏感信息，必须设置强 `UI_PASSWORD`，跨主机使用时应配置 HTTPS。

同步运行会保存 `/api/fs/copy` 返回的 AList/OpenList task ID，并在控制台中展示关联子任务的数量、进度和结果。只有所有文件复制子任务成功，父同步任务才会标记为成功；任一子任务失败或取消，父任务会标记为失败。为避免目录复制动态生成无法关联的内部任务，目录由本程序逐级创建，文件则逐个提交复制。

此版本只支持新版任务 API（`/api/task/copy/*`）以及复制响应中的 `data.tasks[].id`，不兼容旧版 AList 任务接口。

控制台默认关闭，必须设置密码后才能访问：

```sh
export UI_USERNAME=admin
export UI_PASSWORD='请设置一个强密码'
export UI_SESSION_SECRET='请设置一个独立的随机长字符串'
python app.py
```

Docker Compose 用户可以在同目录的 `.env` 中设置：

```env
UI_USERNAME=admin
UI_PASSWORD=请设置一个强密码
UI_SESSION_SECRET=请设置一个独立的随机长字符串
```

运行历史保存在 `/app/data/runtime.db`。使用容器部署时应持久化挂载 `/app/data`；请求中的密码、Token、API Key 等字段会在入库前脱敏。

访问 `/ui` 会进入登录页面，认证成功后的会话有效期为 12 小时。`UI_SESSION_SECRET` 用于签名会话 Cookie；不设置时会在每次进程启动时随机生成，重启后需要重新登录。跨主机或公网访问时仍应配置 HTTPS 反向代理，并设置 `UI_COOKIE_SECURE=true`。

## 贡献

欢迎提交 issue 和 pull request 来改进本项目。

## 许可证

本项目使用AGPL-3.0 license.
Alist2Strm 部分基于同为 AGPL-3.0 的 AutoFilm 项目改写，来源与版权说明见 `THIRD_PARTY_NOTICES.md`。
