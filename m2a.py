import os
import subprocess
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ================= 配置区 =================
# 支持的视频和音频格式
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".ts", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}

# 提取参数：16kHz, 单声道, 无损 PCM WAV (Whisper 最佳输入格式)
FFMPEG_PARAMS = [
    "-vn",                   # 禁用视频
    "-acodec", "pcm_s16le",  # 无损 16-bit PCM
    "-ar", "16000",          # 16kHz 采样率
    "-ac", "1",              # 单声道
    "-loglevel", "error"     # 只显示错误
]
# ==========================================

def run_ffmpeg(input_path, output_path):
    """执行 FFmpeg 提取任务"""
    if os.path.exists(output_path):
        return f"⏭️  跳过: {os.path.basename(output_path)} 已存在"

    cmd = ["ffmpeg", "-y", "-i", str(input_path)] + FFMPEG_PARAMS + [str(output_path)]
    
    try:
        subprocess.run(cmd, check=True)
        return f"✅ 成功: {os.path.basename(input_path)} -> {os.path.basename(output_path)}"
    except subprocess.CalledProcessError as e:
        return f"❌ 失败: {os.path.basename(input_path)} (FFmpeg 报错，文件可能损坏)"

def main():
    parser = argparse.ArgumentParser(description="音视频无损音频提取工具 (专门优化 Whisper 识别)")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-d", "--dir", type=str, help="处理指定目录下的所有文件")
    group.add_argument("-f", "--file", type=str, help="处理单个指定文件")
    
    parser.add_argument("-o", "--output", type=str, help="指定输出目录 (默认保存在原文件所在位置)")
    parser.add_argument("-w", "--workers", type=int, default=4, help="并发线程数 (默认: 4)")

    args = parser.parse_args()

    tasks = []
    
    # --- 模式 1：处理单文件 ---
    if args.file:
        input_file = Path(args.file)
        if not input_file.is_file():
            print(f"❌ 错误: 找不到文件 {args.file}")
            return
        
        out_dir = Path(args.output) if args.output else input_file.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        output_file = out_dir / f"{input_file.stem}_lossless.wav"
        tasks.append((input_file, output_file))

    # --- 模式 2：处理目录 ---
    else:
        input_dir = Path(args.dir)
        if not input_dir.is_dir():
            print(f"❌ 错误: 找不到目录 {args.dir}")
            return
        
        out_dir = Path(args.output) if args.output else input_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        for item in sorted(input_dir.iterdir()):
            if item.is_file() and item.suffix.lower() in VIDEO_EXTS | AUDIO_EXTS:
                # 避免重复处理已经转换过的文件
                if "_lossless.wav" in item.name:
                    continue
                output_file = out_dir / f"{item.stem}_lossless.wav"
                tasks.append((item, output_file))

    if not tasks:
        print("🤷 没有发现需要处理的文件。")
        return

    print(f"🚀 开始提取任务 | 总计: {len(tasks)} 个文件 | 并发线程: {args.workers}")
    print("-" * 50)

    # 使用线程池加速处理 (FFmpeg 转换受限于磁盘 I/O 和 CPU)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_task = {executor.submit(run_ffmpeg, inp, outp): inp for inp, outp in tasks}
        
        for i, future in enumerate(as_completed(future_to_task), 1):
            print(f"[{i:02d}/{len(tasks):02d}] {future.result()}")

    print("-" * 50)
    print("🎉 任务全部完成。")

if __name__ == "__main__":
    main()
