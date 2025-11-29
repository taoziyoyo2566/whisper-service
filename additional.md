#请将此文档作为《架构方案》的附件，交给实施者执行。

-----

# 📘 Whisper 系统实施细则与运维手册 (v1.0)

> **文档说明**：本手册是对《EWAS 架构方案》的补充，专注于生产环境的稳定性、安全性和运维细节。不改变核心架构，旨在提升系统的健壮性。

## 1\. 🛡️ 核心机制增强 (Worker)

### 1.1 任务去重与幂等性 (Idempotency)

为了防止用户疯狂点击提交，导致同一文件多次入队，需在 **Producer (入队端)** 增加 Redis 锁检查。

  * **逻辑**：
    1.  计算任务指纹：`job_id = md5(file_path)`。
    2.  检查 Redis Key `task_lock:{job_id}` 是否存在。
    3.  若存在 -\> 返回 `409 Conflict` (提示：任务已在队列中)。
    4.  若不存在 -\> 入队，并设置 Key，**TTL = 12小时**。
    5.  **完成/失败时**：Worker 主动删除该 Key。

### 1.2 输入资源保护 (Video to Audio)

为了防止 2GB 的视频文件占满 Whisper 服务的带宽，建议在 Stream Proxy (Worker) 层利用 `ffmpeg` 进行流式抽离音频。

  * **优化前**：Alist (视频流) -\> Worker -\> Whisper (接收视频流 -\> 内部转码 -\> 推理)
  * **优化后**：Alist (视频流) -\> Worker (**FFmpeg 提取 MP3 流**) -\> Whisper (接收音频流 -\> 推理)
  * **收益**：传输数据量降低 90%，Whisper 预处理速度提升。

### 1.3 队列背压 (Backpressure)

在 `POST /transcribe` 接口增加硬限制：

```python
MAX_QUEUE_SIZE = 20
if redis_client.llen("whisper_tasks") >= MAX_QUEUE_SIZE:
    return jsonify({"error": "System busy, queue full"}), 503
```

-----

## 2\. ⚙️ 高级配置清单

建议将以下参数加入 `.env` 并注入 Worker，避免硬编码。

| 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `TASK_TIMEOUT` | `7200` | 单个任务最大执行秒数 (2小时) |
| `DOWNLOAD_TIMEOUT` | `60` | Alist 流链接建立超时 |
| `MAX_FILE_SIZE` | `2147483648` | 2GB，超过拒收 |
| `ALLOWED_PATH_PREFIX`| `/guest_upload` | 安全白名单，只允许处理此目录下的文件 |
| `ENABLE_AUDIO_EXTRACT`| `true` | 是否开启 Worker 端预转码 |

-----

## 3\. 🚨 监控与告警策略

不引入复杂的监控栈，利用 **n8n** 作为监控中心。

### 3.1 死信队列与超时告警

  * **Worker 端**：如果任务处理抛出异常，不仅要记录日志，还应将错误信息推送到 Redis 的 `whisper_errors` 列表（保留最近 50 条）。
  * **n8n 端**：创建一个定时任务 (Cron)，每小时检查：
    1.  Worker 容器健康状态。
    2.  Redis 队列是否阻塞（如 `llen > 10` 且持续 1 小时）。

### 3.2 结构化日志 (Structured Logging)

Worker 的日志格式统一调整为 JSON，方便后续（如果需要）接入 ELK。

```json
{
  "time": "2025-01-01T12:00:00Z",
  "level": "INFO",
  "job_id": "a1b2c3d4",
  "event": "task_completed",
  "duration_sec": 450.2,
  "file_size_mb": 500,
  "model": "medium",
  "status": "success"
}
```

-----

## 4\. 🧹 自动清理策略

为了防止服务器硬盘被临时文件填满，需配置自动清理。

### 方案 A：Worker 自清理 (推荐)

Worker 每次启动时（`__main__`），自动运行一次清理逻辑：

1.  调用 Alist API 获取 `/guest_upload` 文件列表。
2.  筛选出所有 `.processing` 结尾的文件。
3.  如果创建时间超过 24 小时 -\> **删除**。

### 方案 B：n8n 定时清理

创建一个 n8n Workflow：

  * **Trigger**: Cron (每天凌晨 4 点)
  * **Action**: HTTP Request -\> Alist API (列出文件)
  * **Function**: JS 代码过滤出 `name.endsWith('.processing')`
  * **Action**: HTTP Request -\> Alist API (删除文件)

-----

## 5\. 🚀 升级与预热 (Warmup)

### 5.1 模型预热脚本

在 Worker 启动后，正式开始消费 Redis 队列前，先执行一次“空跑”。

**`worker/warmup.py`**:

```python
# 生成一个 1秒的静音 MP3，发送给 Whisper 进行一次 tiny 模型推理
# 目的：强制 Whisper 加载库文件进内存，避免第一个用户请求超时
def warmup():
    logger.info("🔥 Warming up Whisper model...")
    dummy_audio = create_1s_silence()
    requests.post(WHISPER_URL, files={'audio_file': dummy_audio}, params={'model': 'tiny'})
```

### 5.2 滚动更新策略

由于是单机 Docker Compose：

1.  `docker-compose pull` 拉取新镜像。
2.  `docker-compose up -d` 重建容器。
3.  **注意**：由于 Worker 重启时 Redis 数据保留，更新过程不会丢失排队中的任务，但**正在处理中**的任务会被中断（需要依赖用户的重试或后续的手动补偿）。

-----

## 6\. 📱 通知 Payload 规范 (Notification Standard)

为了让 n8n 能发送富文本卡片消息，Worker 回调的 Payload 必须包含以下字段：

```json
{
  "event": "task_finished",
  "status": "success",          // success | error
  "file_name": "Meeting.mp4",
  "file_path": "/guest_upload/Meeting.mp4",
  "duration": 320.5,            // 耗时(秒)
  "model_used": "medium",
  "output_files": {
    "vtt": "/guest_upload/Meeting.vtt",
    "srt": "/guest_upload/Meeting.srt"
  },
  "play_url": "http://alist.com/guest_upload/Meeting.mp4", // 预览直链
  "error_msg": null             // 如果失败，填写错误堆栈
}
```

-----

**总结**：
请实施者按此《手册》在编写 `worker.py` 和配置 n8n 时落实上述细节。这不需要改变整体架构图，只是在代码层面增加了健壮性逻辑。