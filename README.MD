# Whisper Service（Alist + Worker + Faster-Whisper + n8n）

基于 `onerahmet/openai-whisper-asr-webservice` 提供推理 API，Alist 提供文件直链与播放器，Worker 负责队列/音频抽取/转码/通知，Streamlit Web UI 负责**从 Alist 列表入队任务**，n8n 使用 PostgreSQL 保存编排数据。

## 依赖
- 已安装 Docker / Docker Compose
- 宿主机开放或本地可用端口：5244（Alist）、5678（n8n）、6379（Redis）、9000（Whisper）、5000（Worker）、8501（Web UI）

## 快速启动（当前服务器已验证可用）
```bash
# 可选：首次运行生成环境文件
cp .env.example .env

# 创建所需数据卷目录并修正权限（容器用户 1000）
mkdir -p data/redis data/models data/media logs alist-data n8n-data postgres-data
chown -R 1000:1000 n8n-data alist-data postgres-data

# 启动
docker compose up -d --build

# 查看状态
docker compose ps
```

## 启动后检查
- `docker compose ps` 状态均为 `Up`/`healthy`。
- 核心健康检查：`curl http://localhost:5000/health`（worker）、`curl http://localhost:9000/`（whisper-service）。
- 如需看日志：`docker compose logs -f worker`、`docker compose logs -f webapp`。

## 服务端点
- whisper-service: http://localhost:9000  （ASR HTTP API）
- webapp: http://localhost:8501          （前端界面，从 Alist 列表入队/下载）
- worker: http://localhost:5000/health    （任务轮询后台）
- alist:  http://localhost:5244           （文件服务）
- n8n:    http://localhost:5678           （自动化编排）
- postgres: 内部服务 `postgres:5432`      （n8n 数据库）
- redis:  localhost:6379                  （内部缓存/队列）

## 目录与卷
- `data/media`：Web UI 内部挂载（兼容保留，可不直接使用）
- `data/models`：Whisper 模型缓存
- `logs`：worker 日志
- `alist-data`：Alist 配置及存储
- `n8n-data`：n8n 配置与数据库（需 UID/GID 1000 权限）
- `postgres-data`：PostgreSQL 数据文件
- `frontend/.streamlit/config.toml`：上传上限（如有需要可调整）

## 使用方式
1) 启动后查看 Alist 初始密码：`docker logs alist | grep -i password`，登录 `http://<server>:5244`，配置本地存储（根目录 `/opt/alist/data`），复制 Admin Token 并写入 `.env` 的 `ALIST_TOKEN`，`docker compose restart worker`。  
2) 上传文件到 `/guest_upload/fast|best|translate`（通过 Alist Web/WebDAV）。fast=base 模型，best=large-v3，translate=英文翻译。  
3) 打开 Web UI `http://<server>:8501`，刷新列表，勾选文件并点击「提交到 Worker」。  
4) 状态流转：`ready -> processing -> completed/failed`。完成后在同目录生成 `.vtt/.srt/.txt`，失败会生成 `.❌失败.txt`。  
5) n8n 可通过 Webhook → HTTP 节点调用 `http://worker:5000/transcribe`（Bearer `WORKER_SECRET`）完成自动化编排；队列满返回 503，幂等锁返回 409。  

API 示例（直接调用 Worker）：
```bash
curl -X POST -H "Authorization: Bearer <WORKER_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"path":"/guest_upload/fast/demo.mp4"}' \
  http://localhost:5000/transcribe
```

## 其他说明
- Worker：音频预提取（ffmpeg）、队列背压（`MAX_QUEUE_SIZE` 默认 20）、幂等锁（路径+大小+mtime，TTL 12h）、失败写入 `whisper_errors` 列表（最近 50 条）。  
- 清理策略：启动时尝试移除 `/guest_upload` 下 24 小时以上的 `.processing` 残留。  
- 健康检查使用容器内 `wget`/`curl`；如需安全加固可在反代层做鉴权或仅绑定内网。  
- 若 Redis 提示 overcommit，可在宿主机执行 `sysctl -w vm.overcommit_memory=1` 并写入 `/etc/sysctl.conf`。  


Ag0jmK1tdocker exec -it alist ./alist admin random