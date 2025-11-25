import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

WHISPER_URL = os.getenv("WHISPER_API_URL", "http://whisper-service:9000").rstrip("/") + "/asr"
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data/media"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "5"))
META_SUFFIX = ".meta.json"

print(f"Worker 启动，监听目录: {DATA_DIR}")


def update_status(meta_path: Path, meta: dict, status: str, error: Optional[str] = None) -> None:
    meta["status"] = status
    if status == "completed":
        meta["completed_at"] = datetime.utcnow().isoformat()
        meta.pop("error", None)
    if error:
        meta["error"] = error
    with meta_path.open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)


def format_timestamp(seconds: float, separator: str) -> str:
    total_millis = int(round(float(seconds) * 1000))
    total_seconds, millis = divmod(total_millis, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}{separator}{millis:03}"


def json_to_srt(data: dict) -> str:
    lines = []
    for idx, segment in enumerate(data.get("segments", []) or [], start=1):
        start = format_timestamp(segment.get("start", 0.0), ",")
        end = format_timestamp(segment.get("end", 0.0), ",")
        text = (segment.get("text") or "").strip()
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines).strip() + "\n"


def json_to_vtt(data: dict) -> str:
    lines = ["WEBVTT", ""]
    for segment in data.get("segments", []) or []:
        start = format_timestamp(segment.get("start", 0.0), ".")
        end = format_timestamp(segment.get("end", 0.0), ".")
        text = (segment.get("text") or "").strip()
        lines.extend([f"{start} --> {end}", text, ""])
    return "\n".join(lines).strip() + "\n"


def list_pending_tasks() -> list[Path]:
    tasks: list[tuple[Path, datetime]] = []
    for meta_file in DATA_DIR.glob(f"*{META_SUFFIX}"):
        try:
            with meta_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("status") == "pending":
                created = data.get("created_at")
                created_dt = datetime.min
                if created:
                    try:
                        created_dt = datetime.fromisoformat(created)
                    except ValueError:
                        created_dt = datetime.min
                tasks.append((meta_file, created_dt))
        except json.JSONDecodeError:
            print(f"跳过损坏的 meta 文件: {meta_file.name}")
    tasks.sort(key=lambda item: item[1])
    return [item[0] for item in tasks]


def process_task(meta_path: Path) -> None:
    try:
        with meta_path.open("r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except Exception as exc:
        print(f"读取元数据失败 {meta_path.name}: {exc}")
        return

    filename = meta.get("filename")
    if not filename:
        print(f"元数据缺少文件名: {meta_path}")
        return

    source_file = DATA_DIR / filename
    if not source_file.exists():
        update_status(meta_path, meta, "failed", "源文件不存在")
        return

    update_status(meta_path, meta, "processing")
    print(f"开始处理: {filename}")

    config = meta.get("config", {})
    params = {
        "task": config.get("task", "transcribe"),
        "output": "json",
    }
    if config.get("language"):
        params["language"] = config["language"]

    try:
        with source_file.open("rb") as stream:
            response = requests.post(
                WHISPER_URL,
                params=params,
                files={"audio_file": stream},
                timeout=3600,
            )

        if response.status_code != 200:
            raise RuntimeError(f"API Error {response.status_code}: {response.text}")

        result = response.json()
        stem = Path(filename).stem
        outputs = config.get("output_formats") or ["srt"]
        output_paths: list[tuple[Path, str]] = []

        if "json" in outputs:
            output_paths.append((DATA_DIR / f"{stem}.json", "json"))
        if "txt" in outputs:
            output_paths.append((DATA_DIR / f"{stem}.txt", "txt"))
        if "srt" in outputs:
            output_paths.append((DATA_DIR / f"{stem}.srt", "srt"))
        if "vtt" in outputs:
            output_paths.append((DATA_DIR / f"{stem}.vtt", "vtt"))

        for target, kind in output_paths:
            if kind == "json":
                with target.open("w", encoding="utf-8") as handle:
                    json.dump(result, handle, ensure_ascii=False, indent=2)
            elif kind == "txt":
                with target.open("w", encoding="utf-8") as handle:
                    handle.write(result.get("text", ""))
            elif kind == "srt":
                with target.open("w", encoding="utf-8") as handle:
                    handle.write(json_to_srt(result))
            elif kind == "vtt":
                with target.open("w", encoding="utf-8") as handle:
                    handle.write(json_to_vtt(result))

        update_status(meta_path, meta, "completed")
        print(f"处理完成: {filename}")

    except requests.RequestException as exc:
        print(f"网络异常: {exc}")
        update_status(meta_path, meta, "failed", str(exc))
    except Exception as exc:
        print(f"处理异常: {exc}")
        update_status(meta_path, meta, "failed", str(exc))


def main() -> None:
    while True:
        pending = list_pending_tasks()
        if pending:
            process_task(pending[0])
        else:
            time.sleep(POLLING_INTERVAL)


if __name__ == "__main__":
    main()
