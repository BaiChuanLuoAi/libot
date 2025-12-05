FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY server.py .
COPY index.html .
COPY video_wan2_2_14B_t2vAPI.json .
COPY video_wan2_2_14B_i2v_API.json .

# 创建必要的目录
RUN mkdir -p files/images logs

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV COMFYUI_API_URL=http://localhost:8188

# 暴露端口
EXPOSE 5010

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5010/ || exit 1

# 启动应用
CMD ["python", "server.py"]

