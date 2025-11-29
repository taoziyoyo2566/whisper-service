import os
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

ALIST_API_URL = os.getenv("ALIST_API_URL", "http://alist:5244").rstrip("/")
ALIST_TOKEN = os.getenv("ALIST_TOKEN", "")
WORKER_API_URL = os.getenv("WORKER_API_URL", "http://worker:5000").rstrip("/")
WORKER_SECRET = os.getenv("WORKER_SECRET", "")
ALLOWED_PATH_PREFIX = os.getenv("ALLOWED_PATH_PREFIX", "/guest_upload").strip() or "/guest_upload"
if not ALLOWED_PATH_PREFIX.startswith("/"):
    ALLOWED_PATH_PREFIX = f"/{ALLOWED_PATH_PREFIX}"
ALLOWED_PATH_PREFIX = ALLOWED_PATH_PREFIX.rstrip("/")

MEDIA_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".aac",
    ".ogg",
    ".wma",
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".ts",
    ".m4v",
    ".webm",
}
RESULT_SUFFIXES = [".vtt", ".srt", ".txt"]
REQUEST_TIMEOUT = (5, 30)


def detect_mode(path: str) -> str:
    lower = path.lower()
    if "/fast/" in lower:
        return "fast"
    if "/best/" in lower:
        return "best"
    if "/translate/" in lower:
        return "translate"
    return "default"


def format_size_mb(size: int) -> str:
    if not size:
        return "0.00"
    return f"{size / (1024 * 1024):.2f}"


def alist_request(method: str, endpoint: str, **kwargs):
    headers = kwargs.pop("headers", {}) or {}
    if ALIST_TOKEN:
        headers["Authorization"] = ALIST_TOKEN
    try:
        resp = requests.request(
            method,
            f"{ALIST_API_URL}{endpoint}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


@st.cache_data(ttl=5)
def list_directory(path: str) -> list[dict]:
    payload = alist_request("POST", "/api/fs/list", json={"path": path, "page": 1, "per_page": 500})
    if not payload or payload.get("code") != 200:
        return []
    return payload.get("data", {}).get("content", []) or []


@st.cache_data(ttl=5)
def load_media_index() -> list[dict]:
    records: list[dict] = []
    targets = [ALLOWED_PATH_PREFIX] + [
        f"{ALLOWED_PATH_PREFIX}/{name}" for name in ("fast", "best", "translate")
    ]
    for folder in targets:
        entries = list_directory(folder)
        by_name = {item.get("name"): item for item in entries if item.get("name")}
        for name, meta in by_name.items():
            if meta.get("is_dir") or meta.get("type") == 1:
                continue
            ext = Path(name).suffix.lower()
            if ext not in MEDIA_EXTENSIONS:
                continue
            stem = Path(name).stem
            status = "ready"
            outputs = {}
            if f"{name}.processing" in by_name:
                status = "processing"
            if f"{name}.❌失败.txt" in by_name:
                status = "failed"
            for suffix in RESULT_SUFFIXES:
                out_name = f"{stem}{suffix}"
                if out_name in by_name:
                    outputs[suffix.lstrip(".")] = f"{folder}/{out_name}"
            if outputs and status == "ready":
                status = "completed"
            records.append(
                {
                    "path": f"{folder}/{name}",
                    "name": name,
                    "dir": folder,
                    "status": status,
                    "mode": detect_mode(folder),
                    "size": meta.get("size", 0),
                    "modified": meta.get("modified", ""),
                    "outputs": outputs,
                }
            )
    return records


@st.cache_data(ttl=60)
def get_download_url(path: str) -> str | None:
    payload = alist_request("POST", "/api/fs/get", json={"path": path})
    if not payload or payload.get("code") != 200:
        return None
    return payload.get("data", {}).get("raw_url")


def queue_task(path: str) -> tuple[int | None, dict]:
    headers = {}
    if WORKER_SECRET:
        headers["Authorization"] = f"Bearer {WORKER_SECRET}"
    try:
        resp = requests.post(
            f"{WORKER_API_URL}/transcribe",
            json={"path": path},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        try:
            body = resp.json()
        except ValueError:
            body = {"error": resp.text}
        return resp.status_code, body
    except Exception as exc:
        return None, {"error": str(exc)}


def status_badge(status: str) -> str:
    mapping = {
        "ready": "🟢 ready",
        "processing": "🟡 processing",
        "completed": "✅ completed",
        "failed": "❌ failed",
    }
    return mapping.get(status, status)


st.set_page_config(page_title="Whisper 自动化控制台", layout="wide")
st.title("🎙️ Whisper 自动化控制台")
st.caption("Alist + Worker 队列（根据 fast/best/translate 自动选模型）")

if st.button("🔄 刷新列表", type="secondary"):
    load_media_index.clear()
    get_download_url.clear()
    st.experimental_rerun()

if not ALIST_TOKEN:
    st.warning("未配置 ALIST_TOKEN，将无法读取 Alist 列表。")

files = load_media_index()

if not files:
    st.info("未在 Alist 中发现媒体文件（检查目录或 Token）。")
    st.stop()

rows = []
for item in files:
    rows.append(
        {
            "选择": False,
            "文件名": item["name"],
            "目录": item["dir"],
            "模式": item["mode"],
            "状态": status_badge(item["status"]),
            "大小 (MB)": format_size_mb(item["size"]),
            "修改时间": item["modified"],
            "路径": item["path"],
        }
    )

df = pd.DataFrame(rows)
editor = st.data_editor(
    df,
    column_config={
        "选择": st.column_config.CheckboxColumn(required=False),
        "路径": st.column_config.TextColumn(help="Worker 处理的完整路径"),
    },
    hide_index=True,
    disabled=["文件名", "目录", "模式", "状态", "大小 (MB)", "修改时间", "路径"],
    use_container_width=True,
)

selected_paths = editor[editor["选择"] == True]["路径"].tolist()

col_left, col_right = st.columns([1, 2])
with col_left:
    if st.button("🚀 提交到 Worker", disabled=not selected_paths):
        results = []
        for path in selected_paths:
            code, body = queue_task(path)
            results.append((path, code, body))
        for path, code, body in results:
            if code == 200:
                st.success(f"{path} 已入队 (job_id: {body.get('job_id')})")
            elif code == 409:
                st.warning(f"{path} 已在队列中")
            else:
                st.error(f"{path} 提交失败 ({code}): {body}")

with col_right:
    st.write("选择文件以查看详情/下载：")
    selected_detail = st.selectbox(
        "文件",
        options=[item["path"] for item in files],
        format_func=lambda p: next((f["name"] for f in files if f["path"] == p), p),
    )
    if selected_detail:
        detail = next((f for f in files if f["path"] == selected_detail), None)
        if detail:
            st.write(f"状态: {status_badge(detail['status'])} | 模式: {detail['mode']}")
            raw_url = get_download_url(detail["path"])
            if raw_url:
                st.markdown(f"[播放/下载原文件]({raw_url})")
            if detail["status"] == "failed":
                fail_path = f"{detail['path']}.❌失败.txt"
                fail_url = get_download_url(fail_path)
                if fail_url:
                    st.markdown(f"[查看失败原因]({fail_url})")
            if detail["outputs"]:
                st.write("输出文件：")
                for label, out_path in detail["outputs"].items():
                    out_url = get_download_url(out_path)
                    if out_url:
                        st.markdown(f"- [{label.upper()}]({out_url})")
            else:
                st.info("尚未生成输出文件。")

st.divider()
st.caption("提示：通过 Alist Web 界面/WebDAV 上传到 /guest_upload/{fast|best|translate}，再在此入队。")
