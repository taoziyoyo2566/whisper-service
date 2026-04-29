set -e

# 准备环境变量文件
if [ ! -f .env ]; then
  cp .env.example .env
fi
UID_VALUE=$(id -u)
GID_VALUE=$(id -g)
sed -i "s/^UID=.*/UID=${UID_VALUE}/" .env
sed -i "s/^GID=.*/GID=${GID_VALUE}/" .env

# 启动服务
# CPU（本地）：  bash init.sh
# GPU（RunPod）：bash init.sh gpu
if [ "${1}" = "gpu" ]; then
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
else
  docker compose up -d
fi
