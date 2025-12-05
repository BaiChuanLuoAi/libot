# 🚀 服务器部署指南

## 快速部署

### 1. 上传代码到服务器
```bash
# 打包上传
tar -czf libot.tar.gz libot/
scp libot.tar.gz user@your-server:/path/to/

# 或使用 git
git clone your-repo
```

### 2. 配置环境变量
```bash
cd libot
cp .env.example .env
nano .env
```

**必填项**:
- `TELEGRAM_BOT_TOKEN` - Bot Token（已预设）
- `ADMIN_IDS` - 你的 Telegram User ID（从 @userinfobot 获取）

### 3. 启动服务
```bash
chmod +x deploy.sh
./deploy.sh
```

或手动启动:
```bash
docker-compose up -d
```

### 4. 查看日志
```bash
# API 服务日志
docker-compose logs -f api-server

# Bot 日志
docker-compose logs -f telegram-bot
```

### 5. 测试
访问: https://t.me/lili_nsfw_gen_bot

发送 `/start`

## 数据持久化

所有数据都保存在本地目录:
- `./files/` - 生成的图片和视频
- `./logs/` - 运行日志
- `./tg_bot/bot_users.db` - 用户数据库
- `./tg_bot/config.env` - Bot 配置

## 管理命令

```bash
# 启动
docker-compose up -d

# 停止
docker-compose down

# 重启
docker-compose restart

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 更新代码后重新构建
docker-compose build
docker-compose up -d
```

## 服务器要求

- **最低配置**: 2GB RAM, 20GB SSD
- **推荐配置**: 4GB RAM, 50GB SSD
- **端口**: 5010（需开放）
- **Docker**: 20.10+
- **Docker Compose**: 2.0+

## 域名配置（可选）

如需配置域名访问图片/视频:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:5010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 故障排查

### 查看容器状态
```bash
docker-compose ps
docker-compose logs telegram-bot --tail 50
```

### 进入容器调试
```bash
docker exec -it lili-bot sh
docker exec -it lili-api sh
```

### 重建数据库
```bash
docker-compose down
rm tg_bot/bot_users.db
docker-compose up -d
```

