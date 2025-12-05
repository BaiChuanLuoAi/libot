FROM python:3.11-slim

WORKDIR /app

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY bot.py .
COPY database.py .
COPY prompts.json .

# 环境变量
ENV PYTHONUNBUFFERED=1

# 启动 Bot
CMD ["python", "bot.py"]

