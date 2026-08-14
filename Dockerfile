# 使用官方的Python基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 复制requirements.txt到工作目录
COPY requirements.txt .

# 安装依赖包
RUN pip install --no-cache-dir -r requirements.txt

# 复制当前目录所有内容到工作目录
COPY . .

# 暴露应用程序运行的端口（根据你的应用程序需要调整）
EXPOSE 8115

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 运行Python应用程序
CMD ["python", "app.py"]
