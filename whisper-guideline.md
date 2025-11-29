这是一个非常棒的思路。既然我们有了 **n8n** 这个强大的流程编排工具，接入各种通知渠道其实非常简单。

**核心原则：让 Python Worker 专注于“干活”，让 n8n 专注于“发消息”。**

你提到的 Telegram、WhatsApp、Line、WeChat（企业微信）等，n8n 都支持。

  * **原生支持（最简单）**：Telegram, Email, Microsoft Teams, Slack, Discord.
  * **Webhook 支持（很简单）**：钉钉 (DingTalk), 飞书 (Feishu), 企业微信 (WeChat Work).
  * **API 支持（稍麻烦）**：WhatsApp (需要 Meta 开发者账号), Line (需要申请 Notify Token), 个人微信 (极难，不建议，建议用企业微信).

为了让方案最通用，我在架构中设计了一个 **“回调机制”**：

1.  Worker 处理完视频后，会主动给 n8n 发一个信号（Webhook）。
2.  n8n 收到信号后，根据你拖拽的节点，自动把消息发给钉钉、飞书、Telegram 或邮件。

-----

以下是为您生成的 **最终执行方案 (Markdown)**。这是一份可以直接交付给技术人员部署，或者你自己复制粘贴就能跑通的完整指南。

# 🚀 Whisper 自动化字幕工厂 (Pro版) - 完整部署指南

本方案基于 **Docker + Alist + n8n + Whisper** 架构。

### ✨ 核心功能亮点

1.  **全自动流式处理**：视频在服务器内部流转，无需本地下载再上传。
2.  **智能参数控制**：
      * 把视频拖进 `/fast` 文件夹 -\> **极速模式** (Base模型)。
      * 把视频拖进 `/best` 文件夹 -\> **高精模式** (Large模型)。
      * 把视频拖进 `/translate` 文件夹 -\> **翻译模式** (自动翻译成英文)。
3.  **所见即所得**：自动生成 VTT 字幕，Alist 网页端点击视频**直接播放带字幕版**。
4.  **全渠道通知**：任务完成后，自动发送消息到 **钉钉、飞书、Telegram、邮件**。

-----

## 🛠️ 第一步：服务器基础环境

确保你的服务器安装了 Docker 和 Docker Compose。

```bash
# 创建项目目录
mkdir -p /opt/whisper-system
cd /opt/whisper-system

# 创建子目录
mkdir -p alist-data n8n-data worker models
```

-----

## 📄 第二步：配置文件编写

### 1\. `docker-compose.yml`

```yaml
services:
  # 1. Alist: 网盘管理与播放器
  alist:
    image: xhofe/alist:latest
    container_name: alist
    restart: always
    volumes:
      - ./alist-data:/opt/alist/data
    ports:
      - "5244:5244"
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=022

  # 2. n8n: 任务编排与通知中心
  n8n:
    image: docker.n8n.io/n8nio/n8n
    container_name: n8n
    restart: always
    ports:
      - "5678:5678"
    environment:
      - N8N_SECURE_COOKIE=false
      - WEBHOOK_URL=http://n8n:5678/
    volumes:
      - ./n8n-data:/home/node/.n8n

  # 3. Whisper Service: 推理引擎
  whisper-service:
    image: onerahmet/openai-whisper-asr-webservice:latest
    container_name: whisper-core
    restart: unless-stopped
    ports:
      - "9000:9000"
    environment:
      - ASR_ENGINE=faster_whisper
      - ASR_MODEL=medium            # 默认模型
      - ASR_QUANTIZATION=int8       # 省内存关键配置
      - ASR_VAD_FILTER=true         # 过滤静音
    volumes:
      - ./models:/root/.cache/whisper

  # 4. Worker: 核心业务逻辑
  worker:
    build: 
      context: ./worker
    container_name: whisper-worker
    restart: always
    ports:
      - "5000:5000"
    environment:
      - ALIST_API_URL=http://alist:5244
      - WHISPER_API_URL=http://whisper-service:9000
      # 这里先留空，等 n8n 设置好后再填入回调地址
      - N8N_CALLBACK_URL=http://n8n:5678/webhook/callback
    env_file:
      - .env
    depends_on:
      - alist
      - whisper-service
```

### 2\. `.env` 文件

```bash
nano .env
```

写入以下内容（`ALIST_TOKEN` 等启动后再填）：

```env
ALIST_TOKEN=
```

-----

## 🐍 第三步：Worker 代码实现

这是系统的核心大脑。

```bash
cd worker
```

### 1\. `Dockerfile`

```dockerfile
FROM python:3.10-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "0", "main:app"]
```

### 2\. `requirements.txt`

```text
flask
requests
gunicorn
```

### 3\. `main.py` (完整逻辑)

```python
import os
import json
import requests
import threading
import logging
import urllib.parse
from flask import Flask, request, jsonify

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 环境变量
ALIST_BASE_URL = os.getenv("ALIST_API_URL", "http://alist:5244")
ALIST_TOKEN = os.getenv("ALIST_TOKEN", "")
WHISPER_BASE_URL = os.getenv("WHISPER_API_URL", "http://whisper-service:9000")
N8N_CALLBACK_URL = os.getenv("N8N_CALLBACK_URL", "")

# --- 核心工具函数 ---

def alist_put_file(file_path, content):
    """上传文件到 Alist"""
    file_path = "/" + file_path.lstrip("/") # 规范路径
    url = f"{ALIST_BASE_URL}/api/fs/put"
    headers = {
        "Authorization": ALIST_TOKEN, 
        "File-Path": urllib.parse.quote(file_path)
    }
    if isinstance(content, str): content = content.encode('utf-8')
    try:
        requests.put(url, headers=headers, data=content)
    except Exception as e:
        logger.error(f"Upload failed: {e}")

def alist_delete_file(dir_path, filenames):
    """删除临时文件"""
    url = f"{ALIST_BASE_URL}/api/fs/remove"
    headers = {"Authorization": ALIST_TOKEN, "Content-Type": "application/json"}
    requests.post(url, headers=headers, json={"names": filenames, "dir": dir_path})

def get_alist_download_url(file_path):
    """获取下载直链"""
    url = f"{ALIST_BASE_URL}/api/fs/get"
    headers = {"Authorization": ALIST_TOKEN, "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json={"path": file_path})
        if resp.status_code == 200 and resp.json().get('code') == 200:
            return resp.json()['data']['raw_url']
    except Exception as e:
        logger.error(f"Get URL failed: {e}")
    return None

def notify_n8n(status, file_name, message, output_dir):
    """回调通知 n8n"""
    if not N8N_CALLBACK_URL: return
    try:
        payload = {
            "status": status,
            "file": file_name,
            "message": message,
            "folder": output_dir,
            "timestamp": os.popen('date -u +"%Y-%m-%dT%H:%M:%SZ"').read().strip()
        }
        requests.post(N8N_CALLBACK_URL, json=payload)
    except Exception as e:
        logger.error(f"Notification failed: {e}")

# --- 转换逻辑 (VTT/SRT) ---

def format_timestamp(seconds):
    milliseconds = int(round(seconds * 1000))
    hours = milliseconds // 3600000
    milliseconds %= 3600000
    minutes = milliseconds // 60000
    milliseconds %= 60000
    seconds = milliseconds // 1000
    milliseconds %= 1000
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"

def json_to_vtt(segments):
    vtt = ["WEBVTT", ""]
    for seg in segments:
        vtt.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}")
        vtt.append(f"{seg['text'].strip()}\n")
    return "\n".join(vtt)

def json_to_srt(segments):
    srt = []
    for i, seg in enumerate(segments, 1):
        ts_start = format_timestamp(seg['start']).replace('.', ',')
        ts_end = format_timestamp(seg['end']).replace('.', ',')
        srt.append(f"{i}\n{ts_start} --> {ts_end}\n{seg['text'].strip()}\n")
    return "\n".join(srt)

# --- 异步任务 ---

def run_task(file_path):
    # 解析路径
    if "/" in file_path:
        output_dir = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)
    else:
        output_dir = "/"
        file_name = file_path
        
    logger.info(f"Task Started: {file_name}")
    status_file = f"{file_name}.🚧处理中"
    alist_put_file(f"{output_dir}/{status_file}", b"Processing...")

    try:
        # 1. 智能参数路由
        model = "medium"
        task_type = "transcribe"
        
        path_lower = file_path.lower()
        if "/fast" in path_lower: model = "base"
        if "/best" in path_lower: model = "large-v3"
        if "/translate" in path_lower: task_type = "translate"

        # 2. 获取流地址
        raw_url = get_alist_download_url(file_path)
        if not raw_url: raise Exception("无法获取下载直链")

        # 3. 流式推理
        whisper_url = f"{WHISPER_BASE_URL}/asr"
        params = {'task': task_type, 'output': 'json', 'model': model}
        
        with requests.get(raw_url, stream=True) as r:
            r.raise_for_status()
            files = {'audio_file': (file_name, r.raw, 'application/octet-stream')}
            resp = requests.post(whisper_url, params=params, files=files, timeout=7200)
            if resp.status_code != 200: raise Exception(f"API Error: {resp.text}")
            result = resp.json()

        # 4. 生成文件
        base_name = os.path.splitext(file_name)[0]
        segments = result.get('segments', [])
        
        # 写入 Alist
        alist_put_file(f"{output_dir}/{base_name}.vtt", json_to_vtt(segments))
        alist_put_file(f"{output_dir}/{base_name}.srt", json_to_srt(segments))
        alist_put_file(f"{output_dir}/{base_name}.txt", result.get('text', ''))
        
        logger.info(f"Task Success: {file_name}")
        notify_n8n("success", file_name, "字幕生成完成，可在线播放。", output_dir)

    except Exception as e:
        logger.error(f"Task Failed: {e}")
        alist_put_file(f"{output_dir}/{file_name}.❌失败.txt", str(e))
        notify_n8n("error", file_name, str(e), output_dir)

    finally:
        alist_delete_file(output_dir, [status_file])

@app.route('/transcribe', methods=['POST'])
def handle_request():
    data = request.json
    file_path = data.get('path')
    if not file_path: return jsonify({"error": "No path"}), 400
    
    # 异步启动
    threading.Thread(target=run_task, args=(file_path,)).start()
    return jsonify({"status": "submitted"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

-----

## 🚀 第四步：启动与初始化

1.  **启动 Alist**：
    ```bash
    docker-compose up -d alist
    ```
2.  **获取 Token**：
      * 查看密码：`docker logs alist`
      * 登录 `http://IP:5244` -\> 管理 -\> 设置 -\> 其他 -\> **复制令牌 (Token)**。
3.  **填入 Token**：
    编辑 `.env` 文件，填入 `ALIST_TOKEN`。
4.  **启动全部服务**：
    ```bash
    docker-compose up -d --build
    ```

-----

## 🔗 第五步：n8n 流程设置 (通知中心)

这是实现你“多渠道通知”的关键步骤。

1.  打开 `http://IP:5678` 注册 n8n。
2.  新建一个 Workflow，命名为 `Whisper Automation`。

### 1\. 触发任务流 (Start)

  * **节点 A (Webhook)**:
      * Path: `transcribe`
      * Method: `POST`
      * *作用：接收用户提交的任务。*
  * **节点 B (HTTP Request)**:
      * URL: `http://worker:5000/transcribe`
      * Method: `POST`
      * Body: JSON `{ "path": "{{ $json.body.path }}" }`
      * *作用：告诉 Worker 开始干活。*

### 2\. 通知回调流 (Callback)

  * **节点 C (Webhook)**:
      * Path: `callback`
      * Method: `POST`
      * *作用：接收 Worker 干完活后的汇报。*
  * **节点 D (Switch)**:
      * 判断 `status` 是 `success` 还是 `error`。

### 3\. 添加通知节点 (根据你的需求连接)

将 **节点 D (Switch)** 的输出连接到以下任意节点：

  * **📱 钉钉 (DingTalk)**:

      * 添加 `HTTP Request` 节点。
      * URL: 你的钉钉机器人 Webhook 地址。
      * Body:
        ```json
        {
          "msgtype": "text",
          "text": { "content": "🎥 字幕任务完成！\n文件：{{ $json.body.file }}\n状态：{{ $json.body.message }}" }
        }
        ```

  * **📘 飞书 (Feishu)**:

      * 添加 `HTTP Request` 节点。
      * URL: 你的飞书机器人 Webhook。
      * Body: `{"msg_type":"text","content":{"text":"字幕已生成: {{ $json.body.file }}"}}`

  * **✈️ Telegram**:

      * 直接添加 n8n 自带的 `Telegram` 节点。
      * 填入 Bot Token 和 Chat ID。
      * Text: `✅ 任务完成：{{ $json.body.file }}`

  * **📧 邮件 (Email)**:

      * 添加 `Send Email` 节点。
      * 配置 SMTP（如 Gmail 或 QQ邮箱）。

-----

## 🎬 最终使用体验

1.  **上传**：在 Alist 的 `/guest_upload/fast` 目录上传一个 `movie.mp4`。
2.  **触发**：调用 n8n 的 webhook（或者做一个简单的 HTML 表单提交 URL）。
3.  **反馈**：
      * Alist 目录立即出现 `movie.mp4.🚧处理中`。
      * 后台自动流式转录，极速模式。
4.  **完成**：
      * 手机 **钉钉/Telegram** 收到弹窗：“🎥 字幕任务完成！文件：movie.mp4”。
      * 刷新 Alist，看到 `movie.vtt` 已生成。
      * **点击 `movie.mp4`，直接开始带字幕播放。**

这套方案完美覆盖了**存储、计算、多用户、多参数、多通知、在线预览**，且完全基于开源组件，无额外费用。