这是一个基于 **Streamlit (前端交互) + Sidecar JSON (状态管理) + Python Worker (后台队列)** 的完整落地执行方案。

该方案针对 **8核 16G 纯 CPU 服务器** 进行了优化，采用 **Faster-Whisper INT8 量化** 技术，确保在无显卡的情况下也能获得约 **4-5倍** 于官方模型的推理速度。

-----

# 🚀 本地化 Whisper 智能字幕生成系统执行方案

## 1\. 系统架构图解

```mermaid
graph TD
    User[用户 (浏览器)] -->|1. 上传/选择文件| Streamlit
    Streamlit -->|2. 生成任务元数据.meta.json| SharedVol((共享存储 /data))
    
    Worker -->|3. 轮询发现 Pending 任务| SharedVol
    Worker -->|4. 发送推理请求| WhisperAPI
    
    WhisperAPI -->|5. 返回识别文本| Worker
    Worker -->|6. 生成 SRT/TXT 并更新状态| SharedVol
    
    Streamlit -->|7. 读取状态与下载成品| SharedVol
```

-----

## 2\. 目录结构准备

在服务器上创建项目目录 `/opt/whisper-system`，并建立以下文件结构：

/opt/whisper-system/
├── docker-compose.yml
├──.env                       \# 环境变量配置
├── frontend/                  \# Streamlit 前端代码
│   ├── Dockerfile
│   ├── app.py
│   └──.streamlit/
│       └── config.toml        \# 解除上传限制
├── worker/                    \# 后台处理脚本
│   ├── Dockerfile
│   └── worker.py
└── data/                      \# 数据挂载目录 (自动生成)
├── media/                 \# 存放音频/视频源文件
└── models/                \# 存放 Whisper 模型缓存

-----

## 3\. 核心配置文件编写

### 3.1 Docker Compose 编排 (`docker-compose.yml`)

此配置定义了三个服务，核心在于通过共享 `/data` 卷打通数据流。

```yaml
services:
  # 1. 推理核心：提供 HTTP API
  whisper-service:
    image: onerahmet/openai-whisper-asr-webservice:latest
    container_name: whisper-core
    restart: unless-stopped
    environment:
      - ASR_ENGINE=faster_whisper   # 关键：使用 CTranslate2 加速引擎
      - ASR_MODEL=medium            # CPU 推荐 medium，平衡速度与精度
      - ASR_QUANTIZATION=int8       # CPU 必须开启 int8 量化，速度提升显著
      - ASR_DEVICE=cpu
    volumes:
      -./data/models:/root/.cache/whisper
    healthcheck:
      test:
      interval: 30s
      timeout: 10s
      retries: 5

  # 2. 前端交互：Streamlit Web UI
  webapp:
    build:./frontend
    container_name: whisper-webui
    restart: unless-stopped
    ports:
      - "8501:8501"
    volumes:
      -./data/media:/app/data/media  # 挂载媒体目录
    environment:
      - DATA_DIR=/app/data/media
    depends_on:
      - whisper-service

  # 3. 任务队列：后台 Worker
  worker:
    build:./worker
    container_name: whisper-worker
    restart: unless-stopped
    volumes:
      -./data/media:/app/data/media  # 必须与前端挂载一致
    environment:
      - WHISPER_API_URL=http://whisper-service:9000
      - DATA_DIR=/app/data/media
      - POLLING_INTERVAL=5
    depends_on:
      - whisper-service
```

### 3.2 前端实现 (`frontend/`)

**`frontend/Dockerfile`**:

```dockerfile
FROM python:3.9-slim
WORKDIR /app
RUN pip install streamlit pandas watchdog
COPY..
CMD ["streamlit", "run", "app.py"]
```

**`frontend/.streamlit/config.toml`** (解决大文件上传限制):

```toml
[server]
maxUploadSize = 2000  # 允许 2GB 上传
address = "0.0.0.0"
```

**`frontend/app.py`**:

```python
import streamlit as st
import os
import json
import pandas as pd
from datetime import datetime
import shutil

# 配置
DATA_DIR = os.getenv("DATA_DIR", "/app/data/media")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(page_title="Whisper 字幕工场", layout="wide")
st.title("🎙️ 本地化 Whisper 字幕生成系统")

# --- 工具函数 ---
def get_file_status(filename):
    meta_path = os.path.join(DATA_DIR, f"{filename}.meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r') as f:
                return json.load(f).get("status", "unknown")
        except:
            return "error"
    return "ready" # 未处理

def create_task(filename, config):
    meta = {
        "filename": filename,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "config": config
    }
    # 原子写入：先写临时文件再重命名，防止 Worker 读到半截文件
    temp_path = os.path.join(DATA_DIR, f"{filename}.meta.tmp")
    final_path = os.path.join(DATA_DIR, f"{filename}.meta.json")
    with open(temp_path, 'w') as f:
        json.dump(meta, f)
    os.rename(temp_path, final_path)

# --- 页面布局 ---
tab1, tab2 = st.tabs(["📤 上传新文件", "🗃️ 文件库与任务队列"])

with tab1:
    uploaded_file = st.file_uploader("上传视频/音频文件", type=['mp4', 'mkv', 'mp3', 'wav', 'm4a'])
    if uploaded_file:
        if st.button("保存到服务器"):
            save_path = os.path.join(DATA_DIR, uploaded_file.name)
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            st.success(f"文件 {uploaded_file.name} 已保存！请去文件库添加任务。")

with tab2:
    st.write("### 服务器文件列表")
    if st.button("🔄 刷新列表"):
        st.rerun()

    # 扫描文件
    files =
    files.sort()

    data =
    for f in files:
        status = get_file_status(f)
        data.append({"文件名": f, "状态": status})

    df = pd.DataFrame(data)
    
    # 交互式表格
    if not df.empty:
        # 使用 data_editor 允许勾选
        df["选择"] = False
        edited_df = st.data_editor(
            df, 
            column_config={
                "选择": st.column_config.CheckboxColumn(required=True),
                "状态": st.column_config.TextColumn(help="ready:待添加, pending:排队中, processing:处理中, completed:已完成")
            },
            disabled=["文件名", "状态"],
            hide_index=True,
        )

        # 任务配置区
        with st.expander("⚙️ 任务参数配置", expanded=True):
            col1, col2 = st.columns(2)
            task_type = col1.selectbox("任务类型", ["transcribe (转录)", "translate (翻译成英文)"], index=0)
            out_fmt = col2.multiselect("输出格式", ["srt", "txt", "json", "vtt"], default=["srt", "txt"])

        # 批量提交按钮
        if st.button("🚀 开始处理选中的文件"):
            selected_files = edited_df[edited_df["选择"] == True]["文件名"].tolist()
            count = 0
            for fname in selected_files:
                # 仅对未处理或已完成（重新生成）的文件操作，跳过正在处理的
                current_status = get_file_status(fname)
                if current_status in ["ready", "completed", "failed"]:
                    config = {
                        "task": task_type.split(),
                        "output_formats": out_fmt
                    }
                    create_task(fname, config)
                    count += 1
            
            if count > 0:
                st.success(f"已将 {count} 个任务加入队列！")
                st.rerun()
            else:
                st.warning("没有选择有效的文件，或文件正在处理中。")
                
        # 结果下载区 (简单的文件链接)
        st.divider()
        st.write("#### 📥 结果下载")
        selected_download = st.selectbox("选择要下载的结果", [f for f in files if get_file_status(f) == "completed"])
        if selected_download:
            base_name = os.path.splitext(selected_download)
            # 寻找生成的 SRT/TXT
            results =
            for res in results:
                with open(os.path.join(DATA_DIR, res), "rb") as f:
                    st.download_button(f"下载 {res}", f, file_name=res)

    else:
        st.info("暂无文件，请先上传。")
```

### 3.3 后台 Worker 实现 (`worker/`)

**`worker/Dockerfile`**:

```dockerfile
FROM python:3.9-slim
WORKDIR /app
RUN pip install requests
COPY..
CMD ["python", "-u", "worker.py"]
```

**`worker/worker.py`**:

```python
import os
import time
import json
import requests
import datetime

# 配置
WHISPER_URL = os.getenv("WHISPER_API_URL", "http://whisper-service:9000") + "/asr"
DATA_DIR = os.getenv("DATA_DIR", "/app/data/media")
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", 5))

print(f"Worker 启动: 监听 {DATA_DIR}")

def update_status(meta_path, meta_data, status, error=None):
    meta_data["status"] = status
    if status == "completed":
        meta_data["completed_at"] = datetime.datetime.now().isoformat()
    if error:
        meta_data["error"] = str(error)
    
    with open(meta_path, 'w') as f:
        json.dump(meta_data, f)

def seconds_to_srt_time(seconds):
    td = datetime.timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    millis = int(td.microseconds / 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def json_to_srt(json_data):
    srt_content = ""
    for idx, segment in enumerate(json_data.get('segments',), 1):
        start = seconds_to_srt_time(segment['start'])
        end = seconds_to_srt_time(segment['end'])
        text = segment['text'].strip()
        srt_content += f"{idx}\n{start} --> {end}\n{text}\n\n"
    return srt_content

def process_task(meta_file):
    meta_path = os.path.join(DATA_DIR, meta_file)
    
    try:
        with open(meta_path, 'r') as f:
            meta = json.load(f)
    except Exception as e:
        print(f"读取元数据失败: {e}")
        return

    filename = meta.get("filename")
    config = meta.get("config", {})
    file_path = os.path.join(DATA_DIR, filename)

    if not os.path.exists(file_path):
        update_status(meta_path, meta, "failed", "源文件不存在")
        return

    print(f"开始处理: {filename}")
    update_status(meta_path, meta, "processing")

    try:
        # 1. 调用 Whisper API
        # 强制请求 JSON 格式，以便 Worker 在本地生成多种格式，减少 API 传输压力
        params = {
            'task': config.get('task', 'transcribe'),
            'output': 'json',
            'language': config.get('language') # 如果为 None 则自动检测
        }
        
        # 移除 None 的参数
        params = {k: v for k, v in params.items() if v is not None}

        with open(file_path, 'rb') as f:
            files = {'audio_file': f}
            response = requests.post(WHISPER_URL, params=params, files=files, timeout=3600) # 1小时超时

        if response.status_code!= 200:
            raise Exception(f"API Error {response.status_code}: {response.text}")

        result = response.json()
        
        # 2. 生成请求的输出格式
        base_name = os.path.splitext(filename)
        output_formats = config.get('output_formats', ['srt'])

        if 'json' in output_formats:
            with open(os.path.join(DATA_DIR, f"{base_name}.json"), 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        
        if 'txt' in output_formats:
            with open(os.path.join(DATA_DIR, f"{base_name}.txt"), 'w', encoding='utf-8') as f:
                f.write(result.get('text', ''))

        if 'srt' in output_formats:
            srt_content = json_to_srt(result)
            with open(os.path.join(DATA_DIR, f"{base_name}.srt"), 'w', encoding='utf-8') as f:
                f.write(srt_content)

        update_status(meta_path, meta, "completed")
        print(f"处理完成: {filename}")

    except Exception as e:
        print(f"处理异常: {e}")
        update_status(meta_path, meta, "failed", str(e))

def main():
    while True:
        # 扫描 pending 任务
        tasks =
        for f in os.listdir(DATA_DIR):
            if f.endswith(".meta.json"):
                try:
                    with open(os.path.join(DATA_DIR, f), 'r') as jf:
                        data = json.load(jf)
                        if data.get("status") == "pending":
                            tasks.append((f, data.get("created_at")))
                except:
                    pass
        
        # 按创建时间排序 (FIFO)
        tasks.sort(key=lambda x: x[1])

        if tasks:
            # 取第一个任务处理
            process_task(tasks)
        else:
            time.sleep(POLLING_INTERVAL)

if __name__ == "__main__":
    main()
```

-----

## 4\. 部署与使用说明

### 部署步骤

1.  将上述文件按目录结构保存。
2.  在 `/opt/whisper-system` 目录下运行：
    ```bash
    docker-compose up -d --build
    ```
3.  **首次启动注意**：服务启动后，`whisper-service` 会自动下载约 1.5GB 的 Medium 模型文件。这需要几分钟，期间前端可能会连接失败，请耐心等待。可以通过 `docker logs -f whisper-core` 查看下载进度。

### 使用流程

1.  **访问 Web UI**：浏览器打开 `http://你的服务器IP:8501`。
2.  **上传/选择**：
      * 方式 A：在 "上传新文件" 标签页上传小文件。
      * 方式 B：直接通过 FTP/SMB 将几十 GB 的大文件放入 `/opt/whisper-system/data/media` 目录，点击 Web UI 上的 "刷新列表" 即可看到。
3.  **发起任务**：在 "文件库" 标签页，勾选一个或多个文件，选择需要的格式（如 SRT），点击 "开始处理"。
4.  **监控进度**：状态会从 `ready` -\> `pending` (排队) -\> `processing` (正在跑) -\> `completed` (完成)。
5.  **下载**：任务完成后，下方会出现下载按钮，直接下载生成的 SRT 字幕。

-----

## 5\. 方案审查与自检 (Self-Correction)

在最终确定方案前，针对潜在问题进行了以下检查与优化：

1.  **Streamlit 重新加载机制问题**：

      * *隐患*：Streamlit 的复选框和按钮在页面刷新（Rerun）后状态容易丢失。
      * *改进*：在 `app.py` 中使用了 `st.data_editor` (Streamlit 1.23+ 新特性)。相比传统的 checkbox 列表，`data_editor` 提供了更稳定的状态保持和更像 Excel 的操作体验，非常适合批量选择文件。

2.  **大文件上传限制**：

      * *隐患*：默认 Streamlit 限制 200MB。
      * *改进*：已在 `config.toml` 中明确配置 `maxUploadSize = 2000` (2GB)。同时推荐用户直接将大文件拷贝到文件夹，这是 Web UI 无法替代的高效路径。

3.  **API 超时问题**：

      * *隐患*：CPU 处理长音频（如 2 小时电影）可能需要 30 分钟以上，普通的 HTTP 客户端默认超时是 60 秒。
      * *改进*：在 `worker.py` 的 `requests.post` 中显式设置了 `timeout=3600` (1 小时)，防止 Worker 误判服务超时而中断。

4.  **并发冲突**：

      * *隐患*：如果有多个文件同时提交，Worker 是否会乱？
      * *改进*：Worker 逻辑是单线程 `while True` 循环，严格串行处理。通过 `sort(key=created_at)` 保证了先来后到的 FIFO 顺序。这在 8核 CPU 上是最稳妥的策略，避免并行挤爆 CPU。

5.  **文件读写竞争**：

      * *隐患*：Web UI 正在写 `.json` 时 Worker 刚好读取。
      * *改进*：采用了 **Write temp -\> Rename** 的原子操作模式，彻底杜绝了读取到损坏 JSON 的可能性。

该方案现在是闭环且健壮的。