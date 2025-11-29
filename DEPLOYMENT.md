# 部署与运维手册（V3.1 Final）

基于 `ARCHITECTURE.md`，提供从需求、环境到运维的完整落地步骤。

## 1. 前置要求
- Docker + Docker Compose。
- 硬件建议：8 核 CPU；内存 ≥ 16GB（跑 large-v3 建议 32GB+）；磁盘 ≥ 100GB；GPU 可选（whisper-service 支持 GPU）。
- 网络：可访问镜像仓库；内部服务无公网依赖。
- 目录规划示例：`/opt/whisper-system/{alist-data,n8n-data,worker,data/models,logs}`。
- 生成密钥：`WORKER_SECRET=$(openssl rand -hex 16)`。

## 2. 环境变量示例（.env）
```
ALIST_API_URL=http://alist:5244
ALIST_TOKEN=your_alist_token
WHISPER_API_URL=http://whisper-service:9000
N8N_CALLBACK_URL=http://n8n:5678/webhook/callback
WORKER_SECRET=your_worker_secret
REDIS_HOST=redis
REDIS_PASSWORD=
MAX_QUEUE_SIZE=20
LOCK_TTL_SECONDS=43200
ALIST_TIMEOUT_CONNECT=5
ALIST_TIMEOUT_READ=60
WHISPER_TIMEOUT_CONNECT=10
WHISPER_TIMEOUT_READ=7200
```

## 3. Docker Compose（服务矩阵）
- 必选：`alist`, `n8n`, `redis`, `whisper-service`, `worker`
- 可选：`webapp`（播放器/管理界面）

示例片段（固定版本 tag，省略无关项）：
```yaml
services:
  redis:
    image: redis:7.2-alpine
    command: ["redis-server", "--save", "60", "1"]
    volumes: ["./data/redis:/data"]
    ports: ["6379:6379"]

  alist:
    image: xhofe/alist:3.29.1
    ports: ["5244:5244"]
    volumes: ["./alist-data:/opt/alist/data"]

  n8n:
    image: docker.n8n.io/n8nio/n8n:1.70.0
    ports: ["5678:5678"]
    volumes: ["./n8n-data:/home/node/.n8n"]

  whisper-service:
    image: onerahmet/openai-whisper-asr-webservice:1.4.0
    ports: ["9000:9000"]
    volumes: ["./data/models:/root/.cache/whisper"]

  worker:
    build: ./worker
    ports: ["5000:5000"]
    env_file: .env
    volumes: ["./logs:/app/logs"]
    depends_on:
      redis: {condition: service_started}
      whisper-service: {condition: service_started}
      alist: {condition: service_started}
```

> 健康检查：为所有服务补 `healthcheck`，worker 使用 `/health`；不要使用 `latest`。

## 4. Worker 代码要求
- Dockerfile：`python:3.10-slim`，安装 `ffmpeg`、`curl`；`pip install -r requirements.txt`；`gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 8 --timeout 0 main:app`。
- 生产者 `/transcribe`：
  - Bearer 鉴权（`WORKER_SECRET`）；路径白名单 `/guest_upload/`，拒绝 `..`。
  - 调 alist 获取元数据，生成 `job_id=md5(path|size|mtime)`。
  - Redis 锁（TTL 12h），存在则 409；`LLEN >= MAX_QUEUE_SIZE` 返回 503。
  - 入队 payload：`job_id, path, timestamp`。
- 消费者：
  - 阻塞出队 → `.processing` 标记 → 直链获取 → ffmpeg 提取音频 `/tmp/{job_id}.mp3` → whisper 推理。
  - 生成 `.vtt/.srt/.txt` 上传原目录；失败写 `{file}.❌失败.txt`。
  - 回调 n8n：`status, job_id, file, model, duration, wait_time, subtitles/play URL（可选）`。
  - 清理临时文件、`.processing`、Redis 锁。
- 预热：启动线程生成 1 秒音频，调用 whisper `model=medium`。

## 5. 处理流程（时序）
1) 用户/前端 → alist 上传文件至 `/guest_upload/{fast,best,translate}`。  
2) n8n Webhook `/transcribe` 收到 `{path}`。  
3) worker `/transcribe`：鉴权→路径校验→元数据→锁→队列→返回 job_id。  
4) worker 消费：`.processing` → 下载 → ffmpeg → whisper → 结果上传。  
5) worker 回调 n8n `/callback`；n8n Switch status→多渠道通知。  
6) 定时任务清理僵尸与过期产物。  
7) 运维监控：日志/队列长度/错误告警。

## 6. n8n 配置指引
- 入口 Webhook `POST /transcribe` → HTTP Request 调 `http://worker:5000/transcribe`（Bearer）。
- 回调 Webhook `POST /callback` → Switch `status` → 钉钉/飞书/Telegram/邮件节点。
- 重试：HTTP 节点启用 `Retry on Fail`，`Max Tries=3`，`Wait=60000ms`。
- 定时清理：Cron 每日 03:00 调 Alist API 清理 `.processing`>24h、误存 `.mp3`、`.json`>7 天。

## 7. 验证与运维
- 启动：`docker-compose up -d --build`
- 健康检查：`curl http://localhost:5000/health`
- 验证：上传小文件 → 触发 Webhook → 看到 `.processing` → 收通知 → 确认 `.vtt/.srt/.txt`。
- 监控：Redis 队列长度、失败率、超时日志；队列满或错误由 n8n 告警。

## 8. 回调 Payload 示例
成功：
```json
{
  "status": "success",
  "job_id": "abc123",
  "file": "movie.mp4",
  "model": "medium",
  "duration": 123.4,
  "wait_time": 5.6,
  "subtitles": {
    "vtt": "https://alist/.../movie.vtt",
    "srt": "https://alist/.../movie.srt",
    "txt": "https://alist/.../movie.txt"
  },
  "play_url": "https://alist/.../movie.mp4"
}
```
失败：
```json
{
  "status": "error",
  "job_id": "abc123",
  "file": "movie.mp4",
  "message": "Whisper Error 500: ...",
  "wait_time": 5.6
}
```

