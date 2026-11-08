from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import threading

import av
import cv2
import numpy as np

from .domain import Evidence, uid


class Cancelled(Exception):
    pass


def check_cancel(cancel: threading.Event):
    if cancel.is_set():
        raise Cancelled("사용자가 분석을 취소했습니다. 원본과 완료된 근거는 보존됩니다.")


def run_process(args: list[str], cancel: threading.Event | None = None, timeout: float = 120):
    """No shell, remote protocols, or unbounded stderr buffers."""
    import tempfile
    import time
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        with subprocess.Popen(args, stdout=output, stderr=error) as proc:
            deadline = time.monotonic() + timeout
            try:
                while proc.poll() is None:
                    if cancel:
                        check_cancel(cancel)
                    if time.monotonic() > deadline:
                        raise TimeoutError("미디어 처리 시간이 초과되었습니다.")
                    threading.Event().wait(0.05)
                if proc.returncode:
                    error.seek(0)
                    detail = error.read(1500).decode("utf-8", errors="replace")
                    raise RuntimeError(f"미디어 처리 실패 ({proc.returncode}): {detail}")
                output.seek(0)
                return output.read()
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


def probe(path: Path) -> dict:
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".mkv", ".mov", ".webm"}:
        raise ValueError("존재하는 MP4/MKV/MOV/WebM 파일을 선택해 주세요.")
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg와 ffprobe를 설치하고 PATH에 추가해 주세요.")
    raw = json.loads(run_process(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                                 "-show_format", "-show_streams", "-of", "json", str(path.resolve())]))
    videos = [s for s in raw["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError("재생 가능한 영상 트랙이 없습니다.")
    video = videos[0]
    duration = float(raw["format"].get("duration", video.get("duration", 0)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("영상 길이를 확인할 수 없습니다.")
    origin = float(raw["format"].get("start_time", 0))
    tracks = []
    for s in raw["streams"]:
        if s["codec_type"] not in {"audio", "video"}:
            continue
        tracks.append({"index": s["index"], "type": s["codec_type"], "codec": s.get("codec_name"),
                       "start": float(s.get("start_time", origin)) - origin,
                       "time_base": s.get("time_base"), "channels": s.get("channels"),
                       "sample_rate": s.get("sample_rate"), "language": s.get("tags", {}).get("language", "미확인")})
    def rate(value):
        try:
            a, b = map(int, value.split("/")); return a / b if b else 0
        except (ValueError, AttributeError):
            return 0
    fps = rate(video.get("avg_frame_rate"))
    return {"duration": duration, "origin": origin, "width": video["width"], "height": video["height"],
            "fps": fps, "nominal_fps": rate(video.get("r_frame_rate")), "video_index": video["index"],
            "tracks": tracks, "size": path.stat().st_size, "sync_measured": False,
            "timing_note": "원본 PTS 기준. 트랙 시작 차이는 측정되지만 장기 드리프트·체감 동기화는 미검증. 정밀 반응 시간 산출 안 함."}


def visual_candidates(path: Path, info: dict, cancel: threading.Event, progress) -> tuple[list[Evidence], dict]:
    candidates: list[Evidence] = []
    previous = None
    next_sample = 0.0
    last_candidate = -5.0
    sampled = decoded = missing_pts = discontinuities = 0
    last_pts = None
    with av.open(str(path), options={"protocol_whitelist": "file,pipe"}) as container:
        stream = container.streams[info["video_index"]]
        stream.thread_count = 2
        for frame in container.decode(stream):
            check_cancel(cancel)
            decoded += 1
            if frame.pts is None:
                missing_pts += 1
                continue
            at = float(frame.pts * frame.time_base) - info["origin"]
            if last_pts is not None and at <= last_pts:
                discontinuities += 1
            last_pts = at
            if at < next_sample or at < 0 or at > info["duration"]:
                continue
            # Decode original PTS; sample only analysis frames, never replace the original.
            next_sample = at + 0.5
            gray = frame.reformat(width=320, height=180, format="gray").to_ndarray()
            sampled += 1
            if previous is not None and at - last_candidate >= 3:
                change = float(np.mean(cv2.absdiff(gray, previous)))
                if change > 32:
                    text = "샘플 프레임 사이에 큰 화면 변화가 관측됨. 전환·이동·피격 등 원인은 확인 필요."
                    if float(gray.mean()) < 8:
                        text = "어두운 화면으로 변화함. 로딩·가림·캡처 중단 여부는 확인 필요."
                    candidates.append(Evidence(uid(), "visual_candidate", max(0, at - 0.5), at,
                                               at, at, text, "frame_difference"))
                    last_candidate = at
            previous = gray
            if sampled % 10 == 0:
                progress(min(60, int(60 * at / info["duration"])), "화면 변화 후보 탐색 중")
    if decoded == 0:
        raise ValueError("영상 프레임을 디코딩하지 못했습니다.")
    return candidates, {"decoded_frames": decoded, "sampled_frames": sampled, "missing_pts": missing_pts,
                        "nonmonotonic_pts": discontinuities, "analysis_sampling_target_hz": 2,
                        "coverage": "전체 원본 보존 · 약 2Hz 분석 샘플링으로 짧은 단서는 누락될 수 있음"}


def extract_speech(path: Path, target: Path, track: dict, cancel: threading.Event, duration: float):
    # Only the derivative is mono. Original channels and all audio tracks stay in the copied media.
    run_process(["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
                 "-i", str(path), "-map", f"0:{track['index']}", "-vn", "-ac", "1", "-ar", "16000",
                 "-af", "aresample=async=1:first_pts=0", "-c:a", "pcm_s16le", str(target)], cancel,
                timeout=max(120, duration * 2))


def export_clip(path: Path, target: Path, start: float, end: float, duration: float):
    from .domain import span
    span(start, end, duration)
    if end <= start:
        raise ValueError("클립 길이는 0보다 커야 합니다.")
    if target.resolve() == path.resolve() or target.exists():
        raise ValueError("원본 또는 기존 파일을 덮어쓸 수 없습니다.")
    partial = target.with_name(target.stem + ".partial.mp4")
    try:
        run_process(["ffmpeg", "-nostdin", "-v", "error", "-n", "-protocol_whitelist", "file,pipe",
                     "-ss", str(start), "-i", str(path), "-t", str(end - start), "-map", "0:v:0",
                     "-map", "0:a?", "-c:v", "libx264", "-preset", "fast", "-threads", "2",
                     "-c:a", "aac", "-movflags", "+faststart", str(partial)], timeout=max(120, (end-start)*3))
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
