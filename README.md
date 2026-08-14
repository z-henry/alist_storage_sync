# alist_storage_sync

![GitHub stars](https://img.shields.io/github/stars/z-henry/alist_storage_sync?style=social)
![GitHub release (latest by date)](https://img.shields.io/github/v/tag/z-henry/alist_storage_sync)
![Docker Pulls](https://img.shields.io/docker/pulls/henryzzzzzz/alist_storage_sync)
![Docker Image Size](https://img.shields.io/docker/image-size/henryzzzzzz/alist_storage_sync/latest)


`alist_storage_sync` 是一个用于定时同步两个 alist 存储的工具，并可以选择扫描已完成的复制任务，刷新缓存并通知 Emby 进行相应文件夹扫描。

## 功能

- **存储同步**: 通过 alist 复制的方式，定时将一个 alist 存储同步到另一个存储上。
- **驱动emby扫描**: 扫描 alist 的已完成的复制任务，刷新 alist 的缓存并通知 Emby 进行对应文件夹扫描。
- **提供webhook**: 扫描 alist 的已完成的复制任务，在复制完后触发。包含同步文件所在的alist根目录信息，方便事件驱动下游流程。
- **alist目录树缓慢缓存**: 定时按照制定的qps，强制刷新指定的alist目录的目录树。
*此功能用于应对115日益严重的风控，需要alist缓存时长设置超长，由此功能刷新缓存（建议qps<=0.5）。缺点是非`alist_storage_sync`同部任务更新到网盘更新的文件，不能及时刷新。依赖于目录树刷新任务的定时，或手动通过alist页面进行刷新*
*搭配[strm生成](https://github.com/Akimio521/AutoFilm)食用*



## 配置

使用前，需要创建一个配置文件 `config.json`。详细的配置说明可以在 [Wiki](https://github.com/z-henry/alist_storage_sync/wiki/配置说明) 中找到。

## 部署

### 本机构建 Docker 镜像

项目根目录的 `build.sh` 会读取 `version.py` 并同时生成版本标签和 `latest` 标签：

```sh
./build.sh
# alist-storage-sync:1.13.0
# alist-storage-sync:latest
```

可以通过环境变量修改镜像名：

```sh
IMAGE_NAME=myrepo/alist-storage-sync ./build.sh
```

### Docker 部署

在使用 Docker 部署 `alist_storage_sync` 前，请确保已经创建好 `config.json` 并放置在合适的路径。

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
       environment:
         - TZ=Asia/Shanghai
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

2. **运行应用**

   使用以下命令运行应用：

   ```sh
   python app.py
   ```

## 浏览器任务控制台

应用启动后访问 `http://<服务器地址>:8115/ui`，可以查看定时任务、运行参数与结果、API 请求以及 Emby/Webhook 回调记录。

运行概览会按具体实例任务（例如某个同步实例、`cache-refresh` 或某个目录树刷新实例）折叠父任务，并在实例标题展示最近执行时间；展开父任务后按需加载对应的 AList 文件子任务。运行记录页面平铺父任务，并支持按实例任务、时间范围和状态筛选。子任务每次加载 100 条，可继续分页加载。

同步运行会保存 `/api/fs/copy` 返回的 AList/OpenList task ID，并在控制台中展示关联子任务的数量、进度和结果。只有所有文件复制子任务成功，父同步任务才会标记为成功；任一子任务失败或取消，父任务会标记为失败。为避免目录复制动态生成无法关联的内部任务，目录由本程序逐级创建，文件则逐个提交复制。

此版本只支持新版任务 API（`/api/task/copy/*`）以及复制响应中的 `data.tasks[].id`，不兼容旧版 AList 任务接口。

控制台默认关闭，必须设置密码后才能访问：

```sh
export UI_USERNAME=admin
export UI_PASSWORD='请设置一个强密码'
python app.py
```

Docker Compose 用户可以在同目录的 `.env` 中设置：

```env
UI_USERNAME=admin
UI_PASSWORD=请设置一个强密码
```

运行历史保存在 `/app/data/runtime.db`。使用容器部署时应持久化挂载 `/app/data`；请求中的密码、Token、API Key 等字段会在入库前脱敏。

Basic Auth 只负责身份校验，不加密传输内容；跨主机或公网访问时，请在应用前配置 HTTPS 反向代理。

## 贡献

欢迎提交 issue 和 pull request 来改进本项目。

## 许可证

本项目使用AGPL-3.0 license.
