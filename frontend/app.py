import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data/media"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

META_SUFFIX = ".meta.json"
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
RESULT_SUFFIXES = [".srt", ".txt", ".json", ".vtt"]
MIME_MAP = {
    ".srt": "text/plain",
    ".txt": "text/plain",
    ".json": "application/json",
    ".vtt": "text/vtt",
}

def trigger_rerun():
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()


def list_media_files():
    files = []
    for entry in DATA_DIR.iterdir():
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix in MEDIA_EXTENSIONS:
            files.append(entry)
    return sorted(files, key=lambda p: p.name.lower())


def get_file_status(filename: str) -> str:
    meta_path = DATA_DIR / f"{filename}{META_SUFFIX}"
    if meta_path.exists():
        try:
            with meta_path.open("r", encoding="utf-8") as handle:
                return json.load(handle).get("status", "unknown")
        except json.JSONDecodeError:
            return "error"
    return "ready"


def create_task(filename: str, config: dict) -> None:
    meta = {
        "filename": filename,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "config": config,
    }
    tmp = DATA_DIR / f"{filename}{META_SUFFIX}.tmp"
    final_path = DATA_DIR / f"{filename}{META_SUFFIX}"
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
    tmp.replace(final_path)


def discover_results(filename: str):
    stem = Path(filename).stem
    found = []
    for suffix in RESULT_SUFFIXES:
        candidate = DATA_DIR / f"{stem}{suffix}"
        if candidate.exists():
            found.append(candidate)
    return found


st.set_page_config(page_title="Whisper 字幕工场", layout="wide")
st.title("🎙️ 本地化 Whisper 字幕生成系统")

# --- 上传入口 ---
tab_upload, tab_tasks = st.tabs(["📤 上传新文件", "🗃️ 文件库与任务队列"])

with tab_upload:
    uploaded = st.file_uploader(
        "上传音频/视频文件", type=[ext.strip(".") for ext in MEDIA_EXTENSIONS]
    )
    if uploaded and st.button("保存到服务器"):
        save_path = DATA_DIR / uploaded.name
        with save_path.open("wb") as handle:
            handle.write(uploaded.getbuffer())
        st.success(f"文件 {uploaded.name} 已保存，前往文件库发起任务。")

with tab_tasks:
    if st.button("🔄 刷新列表"):
        trigger_rerun()

    files = list_media_files()
    if files:
        rows = []
        for file_path in files:
            size_mb = file_path.stat().st_size / (1024 * 1024)
            rows.append(
                {
                    "文件名": file_path.name,
                    "大小 (MB)": f"{size_mb:.2f}",
                    "状态": get_file_status(file_path.name),
                }
            )
        df = pd.DataFrame(rows)
        df["选择"] = False

        editor = st.data_editor(
            df,
            column_config={
                "选择": st.column_config.CheckboxColumn(required=True),
                "状态": st.column_config.TextColumn(
                    help="ready:待添加, pending:排队中, processing:处理中, completed:已完成"
                ),
            },
            hide_index=True,
            disabled=["文件名", "大小 (MB)", "状态"],
            use_container_width=True,
        )

        with st.expander("⚙️ 任务参数配置", expanded=True):
            col1, col2 = st.columns(2)
            task_options = {
                "transcribe": "转录 (transcribe)",
                "translate": "翻译成英文 (translate)",
            }
            task_choice = col1.selectbox(
                "任务类型",
                options=list(task_options.keys()),
                format_func=lambda key: task_options[key],
            )
            output_formats = col2.multiselect(
                "输出格式",
                options=["srt", "txt", "json", "vtt"],
                default=["srt", "txt"],
            )

        if st.button("🚀 开始处理选中的文件"):
            selected_files = editor[editor["选择"] == True]["文件名"].tolist()
            formats = output_formats or ["srt"]
            queued = 0
            for fname in selected_files:
                current_status = get_file_status(fname)
                if current_status in {"ready", "completed", "failed"}:
                    create_task(
                        fname,
                        {
                            "task": task_choice,
                            "output_formats": formats,
                        },
                    )
                    queued += 1
            if queued:
                st.success(f"已将 {queued} 个任务加入队列。")
                trigger_rerun()
            else:
                st.warning("所选文件没有可提交的任务。")

        st.divider()
        st.write("#### 📥 结果下载")
        completed_files = [row["文件名"] for row in rows if get_file_status(row["文件名"]) == "completed"]
        if completed_files:
            selected = st.selectbox("选择已完成的文件", completed_files)
            if selected:
                assets = discover_results(selected)
                if assets:
                    for asset in assets:
                        with asset.open("rb") as handle:
                            st.download_button(
                                f"下载 {asset.name}",
                                handle,
                                file_name=asset.name,
                                mime=MIME_MAP.get(asset.suffix.lower(), "text/plain"),
                            )
                else:
                    st.info("该文件尚未生成输出，稍后再试。")
        else:
            st.info("暂无已完成的任务。")
    else:
        st.info("目录中没有音频/视频文件，请先上传。")
