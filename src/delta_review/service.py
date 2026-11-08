from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import queue
import shutil
import threading
import time

from .analysis import build_report, GOALS
from .domain import ReviewEvent, uid
from .media import Cancelled, check_cancel, extract_speech, probe, visual_candidates
from .storage import Store
from .transcription import LocalWhisper


def import_media(store: Store, source: Path, goal=GOALS[0], demo=False, cancel=None) -> str:
    cancel = cancel or threading.Event()
    info = probe(source)
    check_cancel(cancel)
    size = source.stat().st_size
    limit = store.setting("quota_gb", 50) * 1024**3
    used = sum(p.stat().st_size for p in (store.root / "matches").rglob("*") if p.is_file())
    if used + size > limit or shutil.disk_usage(store.root).free < size + 256 * 1024**2:
        raise OSError("저장 한도 또는 디스크 여유 공간이 부족합니다. 원본은 변경하지 않았습니다.")
    identity = uid()
    folder = store.root / "matches" / identity
    folder.mkdir(parents=True)
    partial = folder / "original.partial"
    target = folder / ("original" + source.suffix.lower())
    digest = hashlib.sha256()
    try:
        with source.open("rb") as src, partial.open("xb") as dst:
            while block := src.read(4 * 1024**2):
                check_cancel(cancel)
                dst.write(block)
                digest.update(block)
            dst.flush()
            os.fsync(dst.fileno())
        if partial.stat().st_size != size:
            raise OSError("복사 중 원본 크기가 변경됐습니다. 녹화를 마친 파일을 선택해 주세요.")
        partial.replace(target)
        info["sha256"] = digest.hexdigest()
        store.add_match(identity, source.stem, target, info, goal, demo)
        return identity
    except BaseException:
        shutil.rmtree(folder)
        raise


def analyse(store: Store, match_id: str, cancel: threading.Event, progress, transcriber=None):
    started = time.monotonic()
    match = store.match(match_id)
    info, path = match["info"], Path(match["media"])
    check_cancel(cancel)
    existing = store.evidence(match_id)
    cached = store.setting(f"visual_metrics:{match_id}")
    if cached is None:
        candidates, metrics = visual_candidates(path, info, cancel, progress)
        for e in candidates:
            # Deterministic IDs avoid duplicates after a crash between annotations and checkpoint.
            e.id = f"{match_id}-visual-{e.end:.6f}"
            event = ReviewEvent(e.id + "-event", max(0, e.start-2), min(info["duration"], e.end+3),
                                e.available_at, "화면 변화 · 원인 확인", evidence_ids=[e.id])
            if not any(old.id == e.id for old in existing):
                store.save_annotation(match_id, e, event)
        store.set_setting(f"visual_metrics:{match_id}", metrics)
    else:
        metrics = dict(cached)
    check_cancel(cancel)
    progress(65, "음성 전사 준비")
    tracks = [t for t in info["tracks"] if t["type"] == "audio"]
    transcripts = [e for e in store.evidence(match_id) if e.kind == "transcript"]
    asr_state = "전사 미실행 · 로컬 모델을 지정하거나 SRT/수동 전사를 추가하세요."
    if transcripts:
        asr_state = f"전사 {len(transcripts)}구간 저장됨 · 수정·확인 여부를 확인하세요."
    elif not tracks:
        asr_state = "음성 트랙 없음 · 시각·수동 근거만 분석"
    elif store.setting("model_path", "") or transcriber:
        track_index = store.setting(f"audio_track:{match_id}", tracks[0]["index"])
        track = next((t for t in tracks if t["index"] == track_index), None)
        if track is None:
            raise ValueError("선택한 음성 트랙이 없습니다. 경기 상세에서 다시 선택하세요.")
        derivative = path.parent / "speech.wav"
        if shutil.disk_usage(store.root).free < info["duration"] * 32000 + 128*1024**2:
            raise OSError("음성 전사용 공간이 부족합니다. 원본과 기존 분석은 보존됩니다.")
        try:
            extract_speech(path, derivative, track, cancel, info["duration"])
            engine = transcriber or LocalWhisper(Path(store.setting("model_path")))
            results = engine.transcribe(derivative, info["duration"], cancel)
            for e in results:
                if e.kind != "transcript":
                    raise ValueError("전사 제공자는 음성 전사 근거만 반환해야 합니다.")
                # A model cannot grant itself human verification or a known speaker.
                e.verified = False
                e.speaker = "미확인"
                e.validate(info["duration"])
            annotations = []
            for e in results:
                event = ReviewEvent(uid(), e.start, min(info["duration"], e.end+3), e.available_at,
                                    "발화 · 내용 확인", evidence_ids=[e.id])
                annotations.append((e, event))
            store.save_annotations(match_id, annotations)
            asr_state = f"로컬 한국어 전사 {len(results)}구간 · 미확인 화자 · 사용자 검토 필요"
        finally:
            derivative.unlink(missing_ok=True)
    check_cancel(cancel)
    progress(90, "시간축·근거 참조 검증")
    revision = store.match(match_id)["revision"]
    metrics["analysis_seconds"] = round(time.monotonic()-started, 2)
    metrics["sync_error_ms"] = None
    metrics["game_performance_impact"] = "미측정 · 실시간 수집 미구현"
    report = build_report(info, store.evidence(match_id), store.events(match_id), match["goal"], asr_state, metrics)
    check_cancel(cancel)
    store.save_report(match_id, report, revision)
    return report


class JobQueue:
    def __init__(self, store: Store):
        self.store = store
        store.recover_jobs()
        self.pending = queue.Queue()
        self.cancels: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.closed = False
        self.thread = threading.Thread(target=self._work, name="delta-analysis", daemon=True)
        self.thread.start()

    def submit(self, match_id):
        with self.lock:
            if self.closed or match_id in self.cancels or not self.store.enqueue(match_id):
                return False
            self.cancels[match_id] = threading.Event()
            self.pending.put(match_id)
            return True

    def cancel(self, match_id):
        with self.lock:
            if match_id in self.cancels:
                self.cancels[match_id].set()

    def active(self, match_id):
        with self.lock:
            return match_id in self.cancels

    def _work(self):
        while (match_id := self.pending.get()) is not None:
            cancel = self.cancels[match_id]
            try:
                self.store.job(match_id, "running", 0, "분석 시작")
                report = analyse(self.store, match_id, cancel,
                                 lambda p, m: self.store.job(match_id, "running", p, m))
                state = "partial" if "미실행" in report["transcription"] else "completed"
                self.store.job(match_id, state, 100, report["transcription"])
            except Cancelled as exc:
                self.store.job(match_id, "cancelled", message=str(exc))
            except Exception as exc:
                self.store.job(match_id, "failed", message=str(exc)[:1600])
            finally:
                with self.lock:
                    self.cancels.pop(match_id, None)
                self.pending.task_done()

    def close(self):
        with self.lock:
            self.closed = True
            for cancel in self.cancels.values():
                cancel.set()
            self.pending.put(None)


def delete_match(store: Store, match_id: str):
    match = store.match(match_id)
    folder = (store.root / "matches" / match_id).resolve()
    if folder.parent != (store.root / "matches").resolve() or Path(match["media"]).resolve().parent != folder:
        raise ValueError("관리되는 경기 폴더가 아닙니다.")
    tombstone = folder.with_name(folder.name + ".deleting")
    folder.rename(tombstone)
    try:
        with store.connect() as db:
            db.execute("DELETE FROM matches WHERE id=?", (match_id,))
            db.execute("DELETE FROM settings WHERE key IN (?,?)", (f"visual_metrics:{match_id}", f"audio_track:{match_id}"))
    except BaseException:
        tombstone.rename(folder)
        raise
    shutil.rmtree(tombstone)


def expired_matches(store: Store):
    cutoff = datetime.now(timezone.utc) - timedelta(days=store.setting("retention_days", 30))
    return [m for m in store.matches() if datetime.fromisoformat(m["created_at"]) < cutoff]
