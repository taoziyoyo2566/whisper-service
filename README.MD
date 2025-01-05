##
sudo chown -R $USER:$USER audio logs transcripts
sudo chmod -R 755 audio logs transcripts

# 停止现有服务
docker-compose down

# 删除现有容器和卷（如果需要完全重置）
docker-compose down -v

# 使用当前用户的 UID 和 GID 启动服务
export UID=$(id -u)
export GID=$(id -g)
docker-compose up -d


# 复制一个音频文件到 audio 目录
cp your_audio_file.mp3 audio/

# 通过 API 上传处理
curl -F "audio_file=@audio/your_audio_file.mp3" http://localhost:9000/asr

#
# 检查转录文件
ls -l transcripts/

# 检查日志
ls -l logs/

#进入容器检查权限
docker-compose exec whisper bash
ls -la /output
ls -la /logs
