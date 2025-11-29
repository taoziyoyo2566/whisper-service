import hashlib
import json
import logging
import os
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

import redis
import requests
from flask import Flask, jsonify, request

# --- Logging ---
os.makedirs("logs", exist_ok=True)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if hasattr(record, "job_id"):
            payload["job_id"] = record.job_id
        if hasattr(record, "path"):
            payload["path"] = record.path
        if hasattr(record, "event"):
            payload["event"] = record.event
        return json.dumps(payload, ensure_ascii=False)


handler = RotatingFileHandler("logs/worker.log", maxBytes=10 * 1024 * 1024, backupCount=5)
handler.setFormatter(JsonFormatter())
console = logging.StreamHandler()
console.setFormatter(JsonFormatter())

logger = logging.getLogger("worker")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.addHandler(console)

app = Flask(__name__)

# --- Environment ---


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


ALIST_BASE_URL = os.getenv("ALIST_API_URL", "http://alist:5244")
ALIST_TOKEN = os.getenv("ALIST_TOKEN", "")
WHISPER_BASE_URL = os.getenv("WHISPER_API_URL", "http://whisper-service:9000")
N8N_CALLBACK_URL = os.getenv("N8N_CALLBACK_URL", "")
WORKER_SECRET = os.getenv("WORKER_SECRET", "")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
MAX_QUEUE_SIZE = _get_int("MAX_QUEUE_SIZE", 20)
LOCK_TTL = _get_int("LOCK_TTL_SECONDS", 12 * 3600)
TASK_TIMEOUT = _get_int("TASK_TIMEOUT", 7200)
ALLOWED_PATH_PREFIX = os.getenv("ALLOWED_PATH_PREFIX", "/guest_upload").strip() or "/guest_upload"
if not ALLOWED_PATH_PREFIX.startswith("/"):
    ALLOWED_PATH_PREFIX = f"/{ALLOWED_PATH_PREFIX}"
ALLOWED_PATH_PREFIX = ALLOWED_PATH_PREFIX.rstrip("/") + "/"
SIZE_LIMIT_BYTES = _get_int("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024)
ENABLE_AUDIO_EXTRACT = _get_bool("ENABLE_AUDIO_EXTRACT", True)
ALIST_TIMEOUT = (
    _get_int("ALIST_TIMEOUT_CONNECT", 5),
    _get_int("ALIST_TIMEOUT_READ", 60),
)
WHISPER_TIMEOUT = (
    _get_int("WHISPER_TIMEOUT_CONNECT", 10),
    _get_int("WHISPER_TIMEOUT_READ", 7200),
)
PROCESSING_CLEAN_THRESHOLD = 24 * 3600
ERROR_LIST_KEY = "whisper_errors"
ERROR_LIST_MAX = 50
QUEUE_KEY = "whisper_tasks"

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=6379,
    db=0,
    password=REDIS_PASSWORD if REDIS_PASSWORD else None,
    decode_responses=True,
)


# --- Helpers ---


def json_response(payload, status=200):
    return jsonify(payload), status


def format_timestamp(seconds: float) -> str:
    milliseconds = int(round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


def json_to_vtt(segments) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments or []:
        start = format_timestamp(seg.get("start", 0.0))
        end = format_timestamp(seg.get("end", 0.0))
        text = (seg.get("text") or "").strip()
        lines.extend([f"{start} --> {end}", text, ""])
    return "\n".join(lines).strip() + "\n"


def json_to_srt(segments) -> str:
    lines = []
    for idx, seg in enumerate(segments or [], start=1):
        start = format_timestamp(seg.get("start", 0.0)).replace(".", ",")
        end = format_timestamp(seg.get("end", 0.0)).replace(".", ",")
        text = (seg.get("text") or "").strip()
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines).strip() + "\n"


def get_job_id(path: str, size: int, modified: str) -> str:
    raw = f"{path}|{size}|{modified}"
    return hashlib.md5(raw.encode()).hexdigest()


def alist_request(method: str, endpoint: str, **kwargs):
    url = f"{ALIST_BASE_URL.rstrip('/')}{endpoint}"
    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = ALIST_TOKEN
    timeout = kwargs.pop("timeout", ALIST_TIMEOUT)
    try:
        return requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    except Exception as exc:
        logger.error(f"Alist request error: {exc}", extra={"job_id": "system"})
        return None


def alist_get_metadata(path: str) -> Optional[dict]:
    resp = alist_request("POST", "/api/fs/get", json={"path": path})
    if not resp or resp.status_code != 200:
        return None
    data = resp.json()
    if data.get("code") != 200:
        return None
    return data.get("data", {})


def alist_put_file(path: str, content: bytes):
    clean = "/" + path.lstrip("/")
    headers = {"File-Path": urllib.parse.quote(clean)}
    alist_request("PUT", "/api/fs/put", data=content, headers=headers)


def alist_delete_file(dir_path: str, names: list[str]):
    alist_request("POST", "/api/fs/remove", json={"names": names, "dir": dir_path})


def alist_list_dir(path: str) -> list[dict]:
    resp = alist_request("POST", "/api/fs/list", json={"path": path, "page": 1, "per_page": 200})
    if not resp or resp.status_code != 200:
        return []
    payload = resp.json()
    if payload.get("code") != 200:
        return []
    data = payload.get("data") or {}
    return data.get("content") or []


def alist_get_download_url(path: str) -> Optional[str]:
    meta = alist_get_metadata(path)
    if not meta:
        return None
    return meta.get("raw_url")


def push_error(job_id: str, file_path: str, message: str):
    try:
        payload = {
            "job_id": job_id,
            "path": file_path,
            "message": message,
            "time": int(time.time()),
        }
        redis_client.lpush(ERROR_LIST_KEY, json.dumps(payload, ensure_ascii=False))
        redis_client.ltrim(ERROR_LIST_KEY, 0, ERROR_LIST_MAX - 1)
    except Exception as exc:
        logger.warning(f"Push error list failed: {exc}", extra={"job_id": job_id, "path": file_path})


def notify_n8n(payload: dict):
    if not N8N_CALLBACK_URL:
        return
    try:
        requests.post(N8N_CALLBACK_URL, json=payload, timeout=(5, 10))
    except Exception as exc:
        logger.error(f"Notify error: {exc}", extra={"job_id": payload.get("job_id", "system")})


def parse_modified_ts(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return None


def cleanup_processing_files():
    cutoff = time.time() - PROCESSING_CLEAN_THRESHOLD
    paths = [ALLOWED_PATH_PREFIX.rstrip("/")]
    for name in ("fast", "best", "translate"):
        paths.append(f"{ALLOWED_PATH_PREFIX.rstrip('/')}/{name}")
    for base in paths:
        entries = alist_list_dir(base)
        for item in entries:
            if item.get("is_dir") or item.get("type") == 1:
                continue
            name = item.get("name", "")
            if not name or not (name.endswith(".processing") or "处理中" in name):
                continue
            modified_ts = parse_modified_ts(item.get("modified") or item.get("mod_time"))
            if modified_ts is None or modified_ts >= cutoff:
                continue
            try:
                alist_delete_file(base, [name])
                logger.info("Cleaned stale marker", extra={"job_id": "system", "path": f"{base}/{name}"})
            except Exception as exc:
                logger.warning(f"Cleanup failed: {exc}", extra={"job_id": "system", "path": f"{base}/{name}"})


def download_source(raw_url: str, job_id: str, suffix: str) -> str:
    output_path = f"/tmp/{job_id}{suffix}"
    with requests.get(raw_url, stream=True, timeout=ALIST_TIMEOUT) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"Download failed with {resp.status_code}")
        with open(output_path, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
    return output_path


def extract_audio(raw_url: str, job_id: str, deadline: Optional[float]) -> tuple[str, float]:
    output_path = f"/tmp/{job_id}.mp3"
    cmd = [
        "ffmpeg",
        "-i",
        raw_url,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        "-y",
        output_path,
    ]
    start = time.time()
    timeout = None
    if deadline:
        timeout = max(deadline - start, 1)
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='ignore')[:200]}")
    return output_path, time.time() - start


def get_whisper_timeout() -> tuple[int, int]:
    connect, read = WHISPER_TIMEOUT
    if TASK_TIMEOUT > 0:
        read = min(read, TASK_TIMEOUT)
    return connect, read


def whisper_infer(audio_path: str, model: str, task: str):
    url = f"{WHISPER_BASE_URL.rstrip('/')}/asr"
    with open(audio_path, "rb") as handle:
        files = {"audio_file": (os.path.basename(audio_path), handle, "audio/mpeg")}
        params = {"task": task, "output": "json", "model": model}
        resp = requests.post(url, params=params, files=files, timeout=get_whisper_timeout())
        if resp.status_code != 200:
            raise RuntimeError(f"Whisper error {resp.status_code}: {resp.text}")
        return resp.json()


def check_timeout(deadline: Optional[float], stage: str):
    if deadline and time.time() > deadline:
        raise TimeoutError(f"Timeout while {stage}")


def process_task(task: dict):
    job_id = task["job_id"]
    file_path = task["path"]
    wait_ms = int((time.time() - task["timestamp"]) * 1000)
    log_ctx = {"job_id": job_id, "path": file_path}
    logger.info(f"Processing task (waited {wait_ms} ms)", extra=log_ctx)

    dir_name = os.path.dirname(file_path) or "/"
    file_name = os.path.basename(file_path)
    status_file = f"{file_name}.processing"
    temp_media = None
    deadline = time.time() + TASK_TIMEOUT if TASK_TIMEOUT > 0 else None

    try:
        alist_put_file(f"{dir_name}/{status_file}", b"Processing...")

        meta = alist_get_metadata(file_path)
        if not meta:
            raise RuntimeError("无法获取文件信息")
        raw_url = meta.get("raw_url")
        if not raw_url:
            raise RuntimeError("缺少下载直链")

        model = "medium"
        task_type = "transcribe"
        lower_path = file_path.lower()
        if "/fast/" in lower_path:
            model = "base"
        elif "/best/" in lower_path:
            model = "large-v3"
        if "/translate/" in lower_path:
            task_type = "translate"

        check_timeout(deadline, "before extract")
        if ENABLE_AUDIO_EXTRACT:
            temp_media, extract_time = extract_audio(raw_url, job_id, deadline)
            logger.info(f"Audio extracted in {extract_time:.2f}s", extra=log_ctx)
        else:
            suffix = os.path.splitext(file_name)[1]
            temp_media = download_source(raw_url, job_id, suffix)
            logger.info("Downloaded source file", extra=log_ctx)

        check_timeout(deadline, "before whisper")
        result = whisper_infer(temp_media, model, task_type)

        base_name = os.path.splitext(file_name)[0]
        segments = result.get("segments", [])
        alist_put_file(f"{dir_name}/{base_name}.vtt", json_to_vtt(segments).encode("utf-8"))
        alist_put_file(f"{dir_name}/{base_name}.srt", json_to_srt(segments).encode("utf-8"))
        alist_put_file(f"{dir_name}/{base_name}.txt", (result.get("text") or "").encode("utf-8"))

        output_urls = {}
        for suffix in ("vtt", "srt", "txt"):
            url = alist_get_download_url(f"{dir_name}/{base_name}.{suffix}")
            if url:
                output_urls[suffix] = url

        duration = time.time() - task["timestamp"]
        payload = {
            "event": "task_finished",
            "status": "success",
            "job_id": job_id,
            "file_name": file_name,
            "file_path": file_path,
            "duration": round(duration, 2),
            "wait_time": round(wait_ms / 1000, 2),
            "model_used": model,
            "task_type": task_type,
            "output_files": output_urls,
            "play_url": raw_url,
            "error_msg": None,
        }
        logger.info("Task success", extra={**log_ctx, "duration": duration})
        notify_n8n(payload)
    except Exception as exc:
        logger.error(f"Task failed: {exc}", extra=log_ctx)
        alist_put_file(f"{dir_name}/{file_name}.❌失败.txt", str(exc).encode("utf-8"))
        notify_n8n(
            {
                "event": "task_finished",
                "status": "error",
                "job_id": job_id,
                "file_name": file_name,
                "file_path": file_path,
                "error_msg": str(exc),
            }
        )
        push_error(job_id, file_path, str(exc))
    finally:
        if temp_media and os.path.exists(temp_media):
            try:
                os.remove(temp_media)
            except OSError:
                pass
        alist_delete_file(dir_name, [status_file])
        redis_client.delete(f"lock:{job_id}")


def consumer_loop():
    logger.info("Worker consumer started", extra={"job_id": "system"})
    while True:
        try:
            task_raw = redis_client.blpop(QUEUE_KEY, timeout=5)
            if not task_raw:
                continue
            task = json.loads(task_raw[1])
            process_task(task)
        except Exception as exc:
            logger.error(f"Consumer loop error: {exc}", extra={"job_id": "system"})
            time.sleep(5)


# --- Routes ---


@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if WORKER_SECRET and request.headers.get("Authorization") != f"Bearer {WORKER_SECRET}":
        return json_response({"error": "Unauthorized"}, 401)

    try:
        path = request.json.get("path")
    except Exception:
        return json_response({"error": "Invalid JSON"}, 400)

    if not path:
        return json_response({"error": "path is required"}, 400)

    clean_path = os.path.normpath(path)
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    if ".." in clean_path or not clean_path.startswith(ALLOWED_PATH_PREFIX):
        return json_response({"error": "Forbidden path"}, 403)

    try:
        qlen = redis_client.llen(QUEUE_KEY)
    except Exception as exc:
        logger.error(f"Redis error: {exc}", extra={"job_id": "system"})
        return json_response({"error": "Queue unavailable"}, 500)

    if qlen >= MAX_QUEUE_SIZE:
        return json_response({"error": "System busy", "queue_length": qlen, "retry_after": 60}, 503)

    meta = alist_get_metadata(clean_path)
    if not meta:
        return json_response({"error": "File not found"}, 404)

    size = int(meta.get("size", 0))
    modified = meta.get("modified", "")
    if size > SIZE_LIMIT_BYTES:
        return json_response({"error": "File too large"}, 413)

    job_id = get_job_id(clean_path, size, modified)
    lock_key = f"lock:{job_id}"

    try:
        if not redis_client.set(lock_key, "1", ex=LOCK_TTL, nx=True):
            return json_response(
                {"status": "ignored", "message": "Task already queued/processing", "job_id": job_id}, 409
            )

        task_payload = {
            "job_id": job_id,
            "path": clean_path,
            "timestamp": time.time(),
        }
        redis_client.rpush(QUEUE_KEY, json.dumps(task_payload))
        position = qlen + 1
        return json_response({"status": "queued", "job_id": job_id, "position": position}, 200)
    except Exception as exc:
        logger.error(f"Queue push error: {exc}", extra={"job_id": "system"})
        redis_client.delete(lock_key)
        return json_response({"error": "Queue error"}, 500)


# --- Warmup ---


def warmup():
    try:
        logger.info("Warmup start", extra={"job_id": "system"})
        dummy = "/tmp/warmup.mp3"
        gen = subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", "sine=f=1000:t=1", "-q:a", "9", "-y", dummy],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if gen.returncode != 0:
            raise RuntimeError("ffmpeg warmup generation failed")
        with open(dummy, "rb") as handle:
            requests.post(
                f"{WHISPER_BASE_URL.rstrip('/')}/asr",
                params={"task": "transcribe", "model": "medium"},
                files={"audio_file": handle},
                timeout=(10, 60),
            )
        try:
            os.remove(dummy)
        except OSError:
            pass
        logger.info("Warmup done", extra={"job_id": "system"})
    except Exception as exc:
        logger.warning(f"Warmup failed: {exc}", extra={"job_id": "system"})


def start_background_workers():
    threading.Thread(target=consumer_loop, daemon=True).start()
    threading.Thread(target=warmup, daemon=True).start()
    threading.Thread(target=cleanup_processing_files, daemon=True).start()


start_background_workers()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
