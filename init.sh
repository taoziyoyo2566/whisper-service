# 设置目录权限
mkdir -p transcripts audio logs
sudo chown -R $USER:$USER transcripts audio logs
sudo chmod -R 755 transcripts audio logs

# 启动服务
export UID=$(id -u)
export GID=$(id -g)
#docker compose up -d
