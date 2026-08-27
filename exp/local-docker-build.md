# 本地 Docker 构建与部署升级

## 适用范围

用于在当前仓库完成修改后：

1. 从本地源码构建 `alist-storage-sync` Docker 镜像。
2. 在 `/usr/mydocker/docker-compose.yml` 中更新 `alist_storage_sync` 服务。
3. 验证新容器使用刚构建的镜像并正常启动。

该流程只使用本地构建的镜像；不推送远程仓库、不拉取远程同名镜像。

`build.sh` 从 `version.py` 读取版本，默认生成：

- `alist-storage-sync:<版本号>`
- `alist-storage-sync:latest`

## 安全规则

- 构建会更新同名本地标签；开始前必须向用户说明实际版本与镜像名。
- 先确认相关测试、`git diff --check` 和工作区状态；未解决的测试失败不能被当作构建成功。
- 不在文档、命令、日志或聊天中记录 sudo 密码、UI 密码、API Key 或 Compose 文件中的其他密钥。
- 需要 sudo 时，使用交互式终端让用户直接输入密码；不得要求用户把密码发送到聊天中。
- 不执行 `docker system prune`、删除镜像、删除容器、`docker compose down`、`docker compose pull` 或远程推送，除非用户明确要求。
- `/usr/mydocker/alist_storage_sync/data` 是运行历史数据库挂载目录；升级时必须保留，不能用空目录覆盖。
- 只更新 `alist_storage_sync` 服务，不改动 `/usr/mydocker` 中其他 Compose 服务。

## 构建前检查

在项目根目录执行：

```bash
pwd
rg -n '^APP_VERSION' version.py
git status --short
git diff --check
docker info
```

按本次改动运行相关测试。若依赖未安装导致测试无法加载，应先安装依赖或明确记录为环境限制；不要把这种失败当作通过。

## 构建本地镜像

用户确认后执行：

```bash
./build.sh
```

若当前用户没有 Docker socket 权限，可由用户在交互式终端执行：

```bash
sudo ./build.sh
```

构建完成后，从 `version.py` 读取实际版本并验证两个标签；不要把示例版本硬编码到命令中：

```bash
version=$(sed -nE "s/^[[:space:]]*APP_VERSION[[:space:]]*=[[:space:]]*['\"]v?([^'\"]+)['\"].*/\1/p" version.py | head -n 1)
docker image inspect "alist-storage-sync:$version"
docker image inspect "alist-storage-sync:latest"
```

只有两个标签都存在且版本一致时，才能开始部署。

## 部署到 /usr/mydocker

目标 Compose 文件已确认是 `/usr/mydocker/docker-compose.yml`，实际服务名是 `alist_storage_sync`，镜像引用是 `alist-storage-sync:latest`。每次部署前仍应重新检查，避免配置变化：

```bash
sudo docker compose -f /usr/mydocker/docker-compose.yml config --services
sudo docker compose -f /usr/mydocker/docker-compose.yml config
```

确认服务名、镜像引用和持久化挂载后，仅重建该服务：

```bash
sudo docker compose -f /usr/mydocker/docker-compose.yml up -d --no-deps --force-recreate --no-build alist_storage_sync
```

不要使用不带服务名的 `docker compose up -d`，否则可能连带更新其他服务。

## 部署验证

部署后执行：

```bash
sudo docker compose -f /usr/mydocker/docker-compose.yml ps alist_storage_sync
sudo docker compose -f /usr/mydocker/docker-compose.yml images alist_storage_sync
sudo docker compose -f /usr/mydocker/docker-compose.yml logs --tail=100 alist_storage_sync
```

验证标准：

- 服务处于运行状态，未持续重启。
- 容器使用刚构建的 `alist-storage-sync:latest` 镜像。
- 最近日志没有启动失败、配置读取失败或数据库迁移失败。
- 访问 UI 后，页面显示的应用版本与 `version.py` 一致。

验证失败时保留容器日志和旧镜像，停止继续操作并报告。未获用户确认前，不删除任何镜像或容器。
