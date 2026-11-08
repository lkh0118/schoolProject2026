from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Protocol

from .domain import Evidence, uid
from .media import check_cancel


class Transcriber(Protocol):
    def transcribe(self, audio: Path, duration: float, cancel: threading.Event) -> list[Evidence]: ...


class LocalWhisper:
    """Explicit local directory only: no implicit model download or upload."""
    def __init__(self, model_path: Path):
        self.path = model_path

    def transcribe(self, audio: Path, duration: float, cancel: threading.Event):
        required = ("model.bin", "config.json", "tokenizer.json")
        if not self.path.is_dir() or any(not (self.path / f).is_file() for f in required):
            raise ValueError("로컬 CTranslate2 모델 폴더에 model.bin, config.json, tokenizer.json이 필요합니다.")
        from faster_whisper import WhisperModel
        check_cancel(cancel)
        model = WhisperModel(str(self.path.resolve()), device="cpu", compute_type="int8", cpu_threads=2,
                             num_workers=1, local_files_only=True)
        segments, _ = model.transcribe(str(audio), language="ko", beam_size=5, vad_filter=True,
                                       condition_on_previous_text=False)
        results = []
        for segment in segments:
            check_cancel(cancel)
            start, end = float(segment.start), float(segment.end)
            # Never silently accept a provider timestamp outside source media.
            evidence = Evidence(uid(), "transcript", start, end, start, end,
                                segment.text.strip(), "local_asr", verified=False)
            evidence.validate(duration)
            results.append(evidence)
        return results


def parse_srt(text: str, duration: float) -> list[Evidence]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("빈 자막 파일입니다.")
    result = []
    stamp = r"(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})"
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        if lines and lines[0].strip().isdigit():
            lines.pop(0)
        if len(lines) < 2:
            raise ValueError("SRT 구간 형식이 올바르지 않습니다.")
        match = re.fullmatch(stamp + r"\s*-->\s*" + stamp, lines[0].strip())
        if not match:
            raise ValueError("SRT 시각 형식이 올바르지 않습니다.")
        parts = list(map(int, match.groups()))
        for offset in (0, 4):
            if parts[offset+1] >= 60 or parts[offset+2] >= 60:
                raise ValueError("자막 분·초는 60 미만이어야 합니다.")
        seconds = lambda p: p[0]*3600 + p[1]*60 + p[2] + p[3]/1000
        start, end = seconds(parts[:4]), seconds(parts[4:])
        item = Evidence(uid(), "transcript", start, end, start, end, "\n".join(lines[1:]), "srt")
        item.validate(duration)
        result.append(item)
    return result
