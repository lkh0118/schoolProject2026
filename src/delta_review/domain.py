from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import math
from uuid import uuid4


def uid() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def span(start: float, end: float, duration: float) -> None:
    if not all(math.isfinite(v) for v in (start, end, duration)):
        raise ValueError("시각은 유한한 숫자여야 합니다.")
    if not 0 <= start <= end <= duration:
        raise ValueError(f"구간은 영상 범위(0–{duration:.2f}초) 안이어야 합니다.")


@dataclass
class Evidence:
    id: str
    kind: str  # visual_candidate, manual, transcript
    start: float
    end: float
    cue_at: float
    available_at: float
    text: str
    source: str  # frame_difference, user, local_asr, srt
    verified: bool = False
    speaker: str = "미확인"
    processed_at: str = field(default_factory=now)
    supersedes: str | None = None

    def validate(self, duration: float) -> None:
        span(self.start, self.end, duration)
        span(self.cue_at, self.available_at, duration)
        if not self.start <= self.cue_at <= self.end:
            raise ValueError("단서 시각은 근거 구간 안이어야 합니다.")
        if self.kind == "transcript" and self.available_at < self.end:
            raise ValueError("전사 문장 전체는 발화 종료 이후에만 사용 가능합니다.")
        if not self.id or not self.text.strip():
            raise ValueError("근거 ID와 내용이 필요합니다.")
        if self.kind not in {"manual", "transcript", "visual_candidate"}:
            raise ValueError("지원하지 않는 근거 분류입니다.")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewEvent:
    id: str
    start: float
    end: float
    decision_at: float
    title: str
    action: str = "사용자 행동 미확인"
    evidence_ids: list[str] = field(default_factory=list)

    def validate(self, duration: float) -> None:
        span(self.start, self.end, duration)
        span(self.start, self.decision_at, self.end)
        if not self.id or not self.title.strip():
            raise ValueError("사건 ID와 제목이 필요합니다.")


def decision_context(evidence: list[Evidence], at: float) -> list[Evidence]:
    """Construct a causally restricted input BEFORE any recommendation is made."""
    available = [e for e in evidence if e.available_at <= at and e.end <= at]
    superseded = {e.supersedes for e in available if e.supersedes}
    return [e for e in available if e.id not in superseded]


class MatchState(StrEnum):
    WAITING = "대기"
    LOADING = "로딩"
    PLAYING = "플레이"
    DOWN = "다운/사망/관전"
    RESULT = "탈출/결과"
    ENDED = "종료 확정"


class MatchLifecycle:
    """Phase-2 foundation, not a claim of implemented screen recognition."""
    def __init__(self) -> None:
        self.state = MatchState.WAITING
        self._result_since: float | None = None

    def observe(self, state: MatchState, timestamp: float, independent_signals: int = 0) -> bool:
        if self.state == MatchState.ENDED:
            return False
        if state == MatchState.RESULT and independent_signals >= 2:
            if self._result_since is None:
                self._result_since = timestamp
            self.state = MatchState.RESULT
            if timestamp - self._result_since >= 3:
                self.state = MatchState.ENDED
                return True
        else:
            self._result_since = None
            if state != MatchState.ENDED:
                self.state = state
        return False

    def manual_end(self) -> bool:
        if self.state == MatchState.ENDED:
            return False
        self.state = MatchState.ENDED
        return True
