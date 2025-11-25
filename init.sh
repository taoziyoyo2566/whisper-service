# 设置目录权限
mkdir -p transcripts audio logs
sudo chown -R $USER:$USER transcripts audio logs
sudo chmod -R 755 transcripts audio logs

# 准备环境变量文件
if [ ! -f .env ]; then
  cp .env.example .env
fi
UID_VALUE=$(id -u)
GID_VALUE=$(id -g)
sed -i "s/^UID=.*/UID=${UID_VALUE}/" .env
sed -i "s/^GID=.*/GID=${GID_VALUE}/" .env

# 启动服务
#docker compose up -d
