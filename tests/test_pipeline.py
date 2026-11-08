import hashlib
from pathlib import Path
import subprocess
import threading
import time
import wave

import numpy as np
import pytest

from delta_review.domain import Evidence, ReviewEvent
from delta_review.media import Cancelled, export_clip, extract_speech, probe
from delta_review.service import JobQueue, analyse, delete_match, import_media
from delta_review.transcription import LocalWhisper


def analyze(store, identity, transcriber=None):
    return analyse(store, identity, threading.Event(), lambda *args: None, transcriber)


def test_actual_import_decode_candidates_report_clip_and_delete(store, video, tmp_path):
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    identity = import_media(store, video, demo=True)
    match = store.match(identity)
    assert Path(match["media"]).read_bytes() == video.read_bytes()
    assert match["info"]["tracks"][1]["channels"] == 2
    result = analyze(store, identity)
    assert result["metrics"]["decoded_frames"] == 120
    assert result["metrics"]["sampled_frames"] >= 7
    assert len(result["reviews"]) == 1
    assert result["reviews"][0]["decision_at"] == pytest.approx(2, abs=.1)
    assert result["reviews"][0]["alternatives"] == []
    assert "미실행" in result["transcription"]
    clip = tmp_path / "clip.mp4"
    export_clip(Path(match["media"]), clip, 1, 3, 4)
    assert probe(clip)["duration"] == pytest.approx(2, abs=.1)
    count = len(store.events(identity))
    analyze(store, identity)
    assert len(store.events(identity)) == count
    delete_match(store, identity)
    assert not store.matches()
    assert not Path(match["media"]).exists()
    assert hashlib.sha256(video.read_bytes()).hexdigest() == digest


def test_annotation_invalidates_report_and_edit_changes_context(store, video):
    identity = import_media(store, video)
    analyze(store, identity)
    e = Evidence("manual", "transcript", .5, 1, .5, 1, "오른쪽", "user", True)
    ev = ReviewEvent("manual-event", 0, 3, 1, "보고 확인", evidence_ids=[e.id])
    store.save_annotation(identity, e, ev)
    assert store.match(identity)["report_stale"]
    result = analyze(store, identity)
    assert not store.match(identity)["report_stale"]
    assert any(r["alternatives"] for r in result["reviews"])
    e.text = "아니, 오른쪽 아님"
    e.verified = False
    store.save_annotation(identity, e)
    assert store.match(identity)["report_stale"]
    result = analyze(store, identity)
    assert not any(r["alternatives"] for r in result["reviews"])


def test_stale_revision_cannot_overwrite_report(store, video):
    identity = import_media(store, video)
    with pytest.raises(ValueError):
        store.save_report(identity, {}, 999)


def test_full_disk_preserves_source_and_no_partial_import(store, video, monkeypatch):
    from collections import namedtuple
    disk = namedtuple("disk", "total used free")
    monkeypatch.setattr("delta_review.service.shutil.disk_usage", lambda _: disk(100, 100, 0))
    with pytest.raises(OSError):
        import_media(store, video)
    assert video.exists() and not store.matches()
    assert not list(store.root.rglob("*.partial"))


def test_database_failure_rolls_back_import(store, video, monkeypatch):
    def failure(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(store, "add_match", failure)
    with pytest.raises(OSError):
        import_media(store, video)
    assert not list((store.root/"matches").iterdir())
    assert video.exists()


def test_provider_failure_preserves_original_and_previous_report(store, video):
    identity = import_media(store, video)
    first = analyze(store, identity)
    class Failing:
        def transcribe(self, *args):
            raise RuntimeError("simulated provider failure, not a real API call")
    with pytest.raises(RuntimeError):
        analyze(store, identity, Failing())
    assert store.match(identity)["report"] == first
    assert Path(store.match(identity)["media"]).exists()
    assert not list(store.root.rglob("speech.wav"))


def test_cancel_does_not_remove_media(store, video):
    identity = import_media(store, video)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        analyse(store, identity, cancel, lambda *args: None)
    assert Path(store.match(identity)["media"]).exists()


def test_duplicate_jobs_and_cancellation(store, video, monkeypatch):
    identity = import_media(store, video)
    started = threading.Event()
    def blocked(store, identity, cancel, progress):
        started.set()
        cancel.wait(5)
        raise Cancelled("test cancellation")
    monkeypatch.setattr("delta_review.service.analyse", blocked)
    jobs = JobQueue(store)
    try:
        assert jobs.submit(identity)
        assert started.wait(2)
        assert not jobs.submit(identity)
        jobs.cancel(identity)
        jobs.pending.join()
        assert store.matches()[0]["state"] == "cancelled"
    finally:
        jobs.close()
        jobs.thread.join(2)


def test_audio_start_offset_preserved_in_asr_derivative(store, tmp_path):
    source = tmp_path / "offset.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=160x90:r=10:d=4",
                    "-itsoffset", "1.2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-threads", "2", "-c:a", "pcm_s16le", str(source)], check=True)
    info = probe(source)
    track = next(t for t in info["tracks"] if t["type"] == "audio")
    assert track["start"] == pytest.approx(1.2, abs=.03)
    target = tmp_path / "speech.wav"
    extract_speech(source, target, track, threading.Event(), info["duration"])
    with wave.open(str(target)) as audio:
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(float)
    assert np.max(np.abs(samples[:16000])) < 2
    assert np.max(np.abs(samples[24000:32000])) > 100


def test_local_model_cannot_trigger_implicit_download(tmp_path):
    with pytest.raises(ValueError, match="로컬"):
        LocalWhisper(tmp_path/"missing").transcribe(tmp_path/"audio.wav", 10, threading.Event())


def test_untrusted_provider_time_rejected_without_persisting(store, video):
    identity = import_media(store, video)
    class Invalid:
        def transcribe(self, *args):
            return [Evidence("bad", "transcript", 100, 101, 100, 101, "가짜 구간", "local_asr")]
    with pytest.raises(ValueError):
        analyze(store, identity, Invalid())
    assert not any(e.id == "bad" for e in store.evidence(identity))


def test_variable_frame_rate_uses_original_timestamps(store, tmp_path):
    import av
    from delta_review.media import visual_candidates
    source = tmp_path / "vfr.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=2",
                    "-vf", "setpts=if(lt(N\\,30)\\,N/(30*TB)\\,(1+(N-30)/10)/TB)",
                    "-fps_mode", "vfr", "-c:v", "libx264", "-threads", "2", str(source)], check=True)
    info = probe(source)
    with av.open(str(source)) as media:
        stamps = [float(f.pts*f.time_base) for f in media.decode(video=0)]
    assert max(np.diff(stamps)) > min(np.diff(stamps)) * 2
    _, metrics = visual_candidates(source, info, threading.Event(), lambda *args: None)
    # FFmpeg's VFR muxer may drop a rounded duplicate PTS from the synthetic setpts
    # expression; compare against the frames actually present in the file.
    assert len(stamps) >= 58
    assert metrics["decoded_frames"] == len(stamps)
    assert metrics["nonmonotonic_pts"] == 0
    assert metrics["sampled_frames"] >= 7


def test_successful_transcriber_contract_uses_real_audio_derivative(store, video):
    """Contract fixture only, not a claimed ASR/model accuracy test."""
    identity = import_media(store, video)
    class ContractFixture:
        def transcribe(self, audio, duration, cancel):
            with wave.open(str(audio)) as wav:
                assert wav.getframerate() == 16000 and wav.getnchannels() == 1
            return [Evidence("test-transcript", "transcript", .5, 1, .5, 1,
                             "[테스트 전사 인터페이스 데이터]", "local_asr")]
    result = analyze(store, identity, ContractFixture())
    assert "전사 1구간" in result["transcription"]
    assert any(e.id == "test-transcript" for e in store.evidence(identity))
    assert all(not r["alternatives"] for r in result["reviews"])


def test_annotation_batch_rolls_back_on_cross_match_collision(store, video):
    first = import_media(store, video)
    second = import_media(store, video)
    existing = Evidence("existing", "transcript", 0, 1, 0, 1, "기존 경기", "user")
    added = Evidence("added", "transcript", 0, 1, 0, 1, "새 경기", "user")
    store.save_annotation(first, existing)
    with pytest.raises(ValueError):
        store.save_annotations(second, [(added, None), (existing, None)])
    assert store.evidence(second) == []
    assert store.evidence(first)[0].text == "기존 경기"
