from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import threading


def default_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "DeltaReview"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "delta-review"


def doctor():
    import psutil
    packages = {}
    for name in ("PySide6", "av", "opencv-python-headless", "faster-whisper"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "미설치"
    return {"os": platform.platform(), "python": platform.python_version(), "cpu_logical": os.cpu_count(),
            "memory_gb": round(psutil.virtual_memory().total/1024**3, 1),
            "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"), "packages": packages,
            "gpu": "미검증 · 기본 분석 CPU, 게임 성능 영향 미측정", "external_upload": "disabled"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Delta Review · 로컬 경기 영상 복기")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="비밀 값 없이 실행 환경 확인")
    imp = sub.add_parser("import", help="녹화 파일을 보관함에 복사")
    imp.add_argument("video", type=Path)
    imp.add_argument("--demo", action="store_true", help="테스트 자료임을 명시")
    analysis = sub.add_parser("analyze", help="저장한 경기 ID를 분석")
    analysis.add_argument("match_id")
    sub.add_parser("list", help="저장된 경기 목록")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        print(json.dumps(doctor(), ensure_ascii=False, indent=2))
        return 0
    from PySide6.QtCore import QLockFile
    from .storage import Store
    store = Store(args.data_dir)
    lock = QLockFile(str(store.root / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        print("이 보관함을 사용하는 Delta Review가 이미 실행 중입니다.", file=sys.stderr)
        return 2
    try:
        if args.command == "import":
            from .service import import_media
            print(import_media(store, args.video, demo=args.demo))
        elif args.command == "analyze":
            from .service import analyse
            store.recover_jobs()
            store.enqueue(args.match_id)
            try:
                result = analyse(store, args.match_id, threading.Event(),
                                 lambda p, m: store.job(args.match_id, "running", p, m))
                store.job(args.match_id, "partial" if "미실행" in result["transcription"] else "completed", 100, result["transcription"])
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except Exception as exc:
                store.job(args.match_id, "failed", message=str(exc))
                raise
        elif args.command == "list":
            print(json.dumps([{k: m[k] for k in ("id", "title", "state", "demo")} for m in store.matches()], ensure_ascii=False, indent=2))
        else:
            # Qt 6.12 can initialize PipeWire when only its library is installed.
            # On minimal Linux images with no client configuration use Qt's supported
            # PulseAudio backend; preserve explicit user settings and Windows defaults.
            if sys.platform.startswith("linux") and not Path("/usr/share/pipewire/client.conf").exists() and not Path("/etc/pipewire/client.conf").exists():
                os.environ.setdefault("QT_AUDIO_BACKEND", "pulseaudio")
            from PySide6.QtWidgets import QApplication
            from .ui import MainWindow
            app = QApplication(sys.argv[:1])
            app.setApplicationName("Delta Review")
            window = MainWindow(store)
            window.show()
            return app.exec()
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
