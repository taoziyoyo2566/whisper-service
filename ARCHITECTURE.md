# Whisper 自动化字幕系统（V3.1 Final）架构说明

## 0. 需求与目标
- 自动转录/翻译 + 在线预览 + 多渠道通知，少人工干预。
- 支持不同优先级/精度：目录路由 `/fast`（base）、`/best`（large-v3）、`/translate`（翻译）。
- 稳定可恢复：幂等、背压、鉴权、日志与告警。
- 部署简易：全 Docker，固定版本 tag。

## 1. 架构概览
- 组件分工：
  - **alist**：文件存储与直链分发。
  - **n8n**：任务入口与通知编排。
  - **whisper-service**：ASR 推理（faster-whisper）。
  - **redis**：队列与幂等锁。
  - **worker**：任务生产/消费、ffmpeg 预处理、结果上传、回调通知。
  - （可选）**webapp**：前端播放器或管理界面。
- 端口（默认）：alist 5244 / n8n 5678 / redis 6379 / whisper 9000 / worker 5000 / webapp 8501。

## 2. ASCII 架构图
```
 User -> Alist(upload) -> n8n Webhook -> worker /transcribe
                                  |            |
                                  |            v
                                  |         Redis queue/lock
                                  v            |
                        n8n callback <- worker consume
                              |               |
                              v               v
                      DingTalk/Feishu/Telegram/email

Alist raw_url -> ffmpeg(audio) -> Whisper -> subtitles -> Alist
```

## 3. 数据与控制流
1) 上传：用户将文件放入 alist 指定目录（目录路由决定模型/任务）。  
2) 触发：n8n Webhook 接收 `{path}` → 调用 worker `/transcribe`（Bearer）。  
3) 入队：worker 校验路径/大小，生成 `job_id=md5(path|size|mtime)`，Redis 锁 + 队列（满> `MAX_QUEUE_SIZE` 返回 503）。  
4) 处理：worker 出队 → `.processing` 标记 → 直链下载 → ffmpeg 提取音频 → whisper 推理 → 生成 `.vtt/.srt/.txt` 上传原目录。  
5) 通知：worker 回调 n8n，n8n 分发钉钉/飞书/Telegram/邮件。  
6) 清理：定时清理僵尸 `.processing`、中间产物、过期文件。  
7) 监控：JSON 日志 + n8n 告警。

## 4. 关键设计
- 幂等/去重：`job_id=md5(path+size+mtime)`，Redis 锁 TTL 12h；锁存在返回 409。
- 背压：`LLEN queue >= MAX_QUEUE_SIZE` 返回 503，附 queue_length/retry_after；n8n 开启重试。
- 预处理：ffmpeg `-vn -acodec libmp3lame -q:a 4` 提取音频，降低带宽/内存。
- 超时：Alist `(5s,60s)`；Whisper `(10s,7200s)`；回调重试由 n8n 处理。
- 安全：Bearer Token + 路径白名单（仅 `/guest_upload/`，拒绝 `..`）；可选 Redis 密码；Alist Token 最小权限。
- 日志/回调：JSON 日志建议字段 `time, level, message, job_id, path, duration, status, error_code`。回调 payload 含 `status, job_id, file, model, duration, wait_time, subtitles/play URL`。
- 健康与预热：worker `/health`；compose healthcheck；启动时用 1 秒音频预热 whisper（至少 medium）。

## 5. 目录与模型路由
- `/fast/` → model `base`（极速）。
- `/best/` → model `large-v3`（高精）。
- `/translate/` → task `translate`（翻译成英文）。
- 其它目录 → model `medium`，task `transcribe`。

## 6. 清理策略
- 启动：扫描并删除过期 `.processing`。
- 定时（建议 n8n Cron 每日 3:00）：
  - `.processing` >24h 删除。
  - 误存中间 `.mp3` 删除。
  - `.json`（原始响应）>7 天删除。
  - 保留 `.srt/.vtt/.txt` 与原视频。

## 7. 部署与配置摘要
- 必填：`ALIST_API_URL`, `ALIST_TOKEN`, `WHISPER_API_URL`, `N8N_CALLBACK_URL`, `WORKER_SECRET`, `REDIS_HOST`。
- 性能/可靠性：`MAX_QUEUE_SIZE`(默认 20), `LOCK_TTL_SECONDS`(默认 43200), 超时配置（Alist/Whisper）。
- 版本：所有镜像固定 tag，禁用 `latest`。

## 8. API 摘要
- `POST /transcribe`（worker）：body `{path}`，Header `Authorization: Bearer WORKER_SECRET`。返回 200 queued / 409 重复 / 503 队列满。
- `POST /callback`（n8n）：worker 回调，payload 见 DEPLOYMENT 中示例，可自定义通知。

