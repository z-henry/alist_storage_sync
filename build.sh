#!/bin/bash

# 检查是否提供了版本号参数
if [ -z "$1" ]; then
    # 如果没有提供参数，则使用当前日期时间作为版本号
    VERSION_TAG=$(date +v%Y%m%d-%H%M)
else
    # 使用提供的参数作为版本号
    VERSION_TAG=$1
fi

# 设置 Docker 镜像名称
IMAGE_NAME="henryzzzzzz/alist_storage_sync"

# 构建 Docker 镜像并打上两个标签
docker build -t $IMAGE_NAME:$VERSION_TAG -t $IMAGE_NAME:latest .

# 输出构建的标签
echo "Built and tagged $IMAGE_NAME:$VERSION_TAG and $IMAGE_NAME:latest"

# 推送版本号标签的镜像
docker push $IMAGE_NAME:$VERSION_TAG

# 推送 latest 标签的镜像
docker push $IMAGE_NAME:latest

# 输出推送完成的信息
echo "Pushed $IMAGE_NAME:$VERSION_TAG and $IMAGE_NAME:latest to Docker Registry"
