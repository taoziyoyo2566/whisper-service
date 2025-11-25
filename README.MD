# Whisper CPU 全流程（Streamlit + Faster-Whisper + Worker）

基于 `onerahmet/openai-whisper-asr-webservice` 提供推理 HTTP API，前端用 Streamlit，Worker 串行轮询处理任务。针对 8C16G CPU，启用了 Faster-Whisper int8。

## 快速启动

```bash
# 可选：首次运行创建 .env（当前 compose 未读取该文件，可留空）
cp .env.example .env

# 构建并启动三个服务
docker-compose up -d --build
```

服务：
- whisper-service: http://localhost:9000 （HTTP API）
- webapp: http://localhost:8501 （上传/下发任务/下载结果）
- worker: 后台轮询，串行执行任务

## 目录与卷

- `data/media`：音频/视频源文件及生成的 SRT/TXT/JSON/VTT
- `data/models`：Whisper 模型缓存
- `frontend/.streamlit/config.toml`：上传上限 2GB

## 使用方式

1) 访问 `http://<server>:8501`  
2) 在「上传新文件」选择音视频并保存，或直接把大文件放进 `data/media/` 再点击「刷新列表」  
3) 在「文件库与任务队列」勾选文件，选择任务类型（转录/翻译）和输出格式（srt/txt/json/vtt），点击「开始处理」  
4) 状态从 `ready -> pending -> processing -> completed`，完成后在下方下载生成文件  

API 直连（绕过前端）：
```bash
curl -F "audio_file=@data/media/your_audio_file.mp3" "http://localhost:9000/asr?task=transcribe&output=json"
```

## 其他说明

- 健康检查改用内置 Python，避免依赖镜像是否自带 curl。  
- 2GB 上传上限已在前端配置；更大文件建议直接放入 `data/media`。  
- 默认端口直接暴露，如需加固可在反代层增加鉴权或仅绑定内网。  
