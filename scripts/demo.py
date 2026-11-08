"""Explicit synthetic media only. Never presents generated data as actual gameplay."""
from pathlib import Path
import argparse
import subprocess
import threading

from delta_review.domain import Evidence, ReviewEvent, uid
from delta_review.service import analyse, import_media
from delta_review.storage import Store


def create_demo(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    media = root / "SYNTHETIC_DEMO_NOT_GAMEPLAY.mp4"
    if not media.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i",
                        "testsrc2=s=960x540:r=30:d=8", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                        "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(media)], check=True)
    store = Store(root / "library")
    identity = import_media(store, media, demo=True)
    evidence = Evidence(uid(), "transcript", 1, 3, 1, 3,
                        "[수동 데모 자막] 영상은 테스트 패턴이며 실제 경기·브리핑이 아닙니다.", "user", True)
    event = ReviewEvent(uid(), 0, 6, 3, "테스트 구간 · 실제 경기 아님", evidence_ids=[evidence.id])
    store.save_annotation(identity, evidence, event)
    store.enqueue(identity)
    result = analyse(store, identity, threading.Event(), lambda *args: None)
    store.job(identity, "completed", 100, result["transcription"])
    return store, identity


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".delta-review/demo"))
    args = parser.parse_args()
    store, identity = create_demo(args.output.resolve())
    print(f"테스트 데모 생성됨: {identity}\n실행: python -m delta_review --data-dir \"{store.root}\"")
