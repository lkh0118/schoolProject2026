from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Protocol

from .domain import Evidence, ReviewEvent, decision_context, now, span

GOALS = ("생존·탈출", "임무 진행", "전리품 보존", "아군 지원")


class TacticalProvider(Protocol):
    """An optional provider sees ONLY the past context; never the whole match or a media path.

    No network provider is enabled or implemented in phase 1. OCR/transcript strings
    are untrusted data, never instructions or executable tools.
    """
    def compare(self, context: list[dict], goal: str) -> list[dict]: ...


class ConservativePlanner:
    def compare(self, context: list[dict], goal: str) -> list[dict]:
        reviewed = [e for e in context if e["verified"] and e["kind"] in {"manual", "transcript"}]
        if not reviewed:
            return []
        refs = [e["id"] for e in reviewed]
        # These are explicitly conditional comparison prompts, not game-specific detection.
        options = [
            {"action": "진행을 잠시 멈추고 단서의 방향·기준을 확인하는 선택",
             "premise": "정지 가능한 안전한 위치와 확인할 시간이 있을 때만 실행 가능",
             "benefit": "불명확한 정보로 이동을 결정할 위험을 줄일 수 있음",
             "risk": "멈추는 위치가 노출돼 있거나 시간이 촉박하면 더 위험할 수 있음",
             "switch_when": "현재 위치가 위험하거나 즉시 이동해야 하면 이동 선택을 우선 비교",
             "evidence_ids": refs},
            {"action": f"{goal} 목표에 따라 이동을 이어가며 아군에게 재확인하는 선택",
             "premise": "이동 경로를 직접 확인했고 브리핑을 요청할 여유가 있을 때만 실행 가능",
             "benefit": "목표 진행을 유지하면서 추가 정보를 요청할 수 있음",
             "risk": "이동 중 주의가 나뉘고 추가 위협을 놓칠 수 있음",
             "switch_when": "경로나 정보가 불확실하면 안전한 곳에서 확인하는 선택을 우선 비교",
             "evidence_ids": refs},
        ]
        return options


def build_report(info: dict, evidence: list[Evidence], events: list[ReviewEvent], goal: str,
                 transcription_state: str, metrics: dict, planner: TacticalProvider | None = None) -> dict:
    if goal not in GOALS:
        raise ValueError("지원하지 않는 경기 목표입니다.")
    duration = info["duration"]
    index = {e.id: e for e in evidence}
    if len(index) != len(evidence):
        raise ValueError("중복 근거 ID입니다.")
    for e in evidence:
        e.validate(duration)
        if e.supersedes:
            previous = index.get(e.supersedes)
            if previous is None or previous.available_at >= e.available_at:
                raise ValueError("정정 대상은 같은 경기의 이전 발화여야 합니다.")
    planner = planner or ConservativePlanner()
    reviews = []
    for event in events:
        event.validate(duration)
        if any(ref not in index for ref in event.evidence_ids):
            raise ValueError("존재하지 않는 근거를 참조한 사건입니다.")
        context = decision_context(evidence, event.decision_at)
        # Temporal neighbourhood prevents a very old briefing becoming a current assertion.
        context = [e for e in context if e.end >= max(0, event.start - 15)]
        context_ids = {e.id for e in context}
        reviewed = [e for e in context if e.verified]
        options = planner.compare([e.to_dict() for e in context], goal)
        for option in options:
            if not option.get("evidence_ids") or not set(option["evidence_ids"]) <= context_ids:
                raise ValueError("대안에 미래 또는 존재하지 않는 근거가 포함되었습니다.")
            if any(not index[r].verified for r in option["evidence_ids"]):
                raise ValueError("확인하지 않은 전사·후보만으로 대안을 생성할 수 없습니다.")
            if not all(isinstance(option.get(k), str) and option[k].strip()
                       for k in ("action", "premise", "benefit", "risk", "switch_when")):
                raise ValueError("대안에 전제·이점·위험·전환 조건이 필요합니다.")
        if options and not 2 <= len(options) <= 3:
            raise ValueError("대안은 2~3개여야 합니다.")
        reviews.append({
            **asdict(event), "facts": [e.to_dict() for e in context if e.kind != "transcript"],
            "briefings": [e.to_dict() for e in context if e.kind == "transcript"],
            "context_ids": sorted(context_ids), "alternatives": options,
            "inference": "장면별 최적 행동은 판단 보류. 아래는 사용자가 검토할 조건부 비교 틀입니다.",
            "unknowns": ["화면 밖 위치와 소리의 층수·거리는 확정하지 않음",
                         "음성 존재는 실제 청취·이해를 의미하지 않음. 발화 내용의 사실 여부도 미검증",
                         "원본의 장기 동기화 오차 미측정 · 정밀 반응 시간 산출 안 함"],
            "confidence": "낮음", "reviewed_evidence": len(reviewed),
            "next_action": "표시된 구간을 재생해 직접 관찰과 브리핑을 확인하고, 안전한 위치에서 정보 재확인을 연습할지 결정하세요."
                           if reviewed else "원본을 재생하고 단서의 시각·내용을 확인한 뒤 사건 또는 전사를 수정해 주세요.",
            "after_decision": [e.to_dict() for e in evidence if e.available_at > event.decision_at and event.start <= e.start <= event.end],
        })
    report = {"schema_version": 1, "created_at": now(), "goal": goal,
              "summary": f"복기 후보 {len(reviews)}개 · 확인된 근거 {sum(e.verified for e in evidence)}개. 전술 판정은 사용자 검토가 필요합니다.",
              "strengths": "잘한 판단을 자동 확정할 검증된 게임 전용 분석기가 아직 없습니다.",
              "priorities": [r["id"] for r in sorted(reviews, key=lambda r: (-r["reviewed_evidence"], r["start"]))[:3]],
              "reviews": reviews, "transcription": transcription_state, "metrics": metrics,
              "limitations": ["화면 변화는 사건 후보이며 적·교전·실수 탐지가 아닙니다.",
                              "음성 트랙은 혼합될 수 있으며 화자는 미확인입니다.",
                              "자동 전사와 가져온 자막은 사용자 확인 전까지 전술 근거에서 보류됩니다.",
                              "게임별 객체·HUD·환경음 인식과 외부 멀티모달 분석은 미구현입니다."],
              "external_upload_bytes": 0}
    validate_report(report, evidence, duration)
    return report


def validate_report(report: dict, evidence: list[Evidence], duration: float):
    index = {e.id: e for e in evidence}
    for item in report["reviews"]:
        span(item["start"], item["end"], duration)
        span(item["start"], item["decision_at"], item["end"])
        for ref in item["evidence_ids"] + item["context_ids"]:
            if ref not in index:
                raise ValueError("보고서가 없는 근거를 참조합니다.")
        for ref in item["context_ids"]:
            if index[ref].available_at > item["decision_at"] or index[ref].end > item["decision_at"]:
                raise ValueError("미래 정보가 판단 문맥에 포함됐습니다.")
        for option in item["alternatives"]:
            if not set(option["evidence_ids"]) <= set(item["context_ids"]):
                raise ValueError("대안이 당시 이용할 수 없는 정보를 사용합니다.")


def aim_report(profile: dict | None, samples: list[dict]) -> dict:
    valid = [s for s in samples if s.get("reviewed") and not s.get("confounders") and s.get("fps", 0) >= 60]
    counts = Counter(s.get("observation", "미분류") for s in valid)
    return {"status": "판단할 자료 부족", "numeric_candidates": [], "valid_samples": len(valid),
            "excluded_samples": len(samples) - len(valid), "observations": dict(counts),
            "reason": "현재 설정이 필요합니다." if not profile else
                      "수동 관찰만 저장된 단계입니다. 표적 추적·반동 분리·반복 측정 검증 전에는 수치 감도를 추천하지 않습니다.",
            "protocol": ["동일한 표적·거리·무기·배율을 유지합니다.",
                         "현재 감도와 소폭 후보를 교차 순서로 비교하고 후보마다 적응 시간을 둡니다.",
                         "각 조건을 반복해 포착 속도·정확도·체감 난이도를 기록합니다.",
                         "배율별로 따로 평가하고 설정은 사용자가 직접 적용합니다."]}
