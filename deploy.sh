#!/bin/bash

echo "=========================================="
echo "  Lili AI - Docker Deployment"
echo "=========================================="
echo ""

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found!"
    echo "Creating from example..."
    cp .env.example .env
    echo ""
    echo "📝 Please edit .env file with your configuration:"
    echo "   - Set your ADMIN_IDS (get from @userinfobot)"
    echo "   - Configure payment gateways if needed"
    echo ""
    read -p "Press Enter after editing .env to continue..."
fi

# 加载环境变量
export $(cat .env | grep -v '^#' | xargs)

echo "🔧 Building Docker images..."
docker-compose build

echo ""
echo "🚀 Starting services..."
docker-compose up -d

echo ""
echo "⏳ Waiting for services to be ready..."
sleep 5

# 检查服务状态
echo ""
echo "📊 Service Status:"
docker-compose ps

echo ""
echo "✅ Deployment complete!"
echo ""
echo "🔍 Check logs:"
echo "   docker-compose logs -f api-server"
echo "   docker-compose logs -f telegram-bot"
echo ""
echo "🛑 Stop services:"
echo "   docker-compose down"
echo ""
echo "📱 Test bot: https://t.me/lili_nsfw_gen_bot"
echo ""

