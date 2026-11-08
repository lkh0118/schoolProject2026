from __future__ import annotations

from collections import Counter
from pathlib import Path
import json

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox,
                              QScrollArea, QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

from .analysis import aim_report
from .service import delete_match, expired_matches
from .ui_support import label, seconds_box


class AimPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.store = window.store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.addWidget(label("감도를 바꾸기 전에, 관찰부터.", "heading"))
        layout.addWidget(label("설정을 바꾸면 새 프로필로 저장합니다. 배율과 프로필이 다른 표본은 합치지 않습니다.", "muted"))
        row = QHBoxLayout()
        self.profiles = QComboBox()
        self.profiles.currentIndexChanged.connect(self.report)
        row.addWidget(self.profiles, 1)
        row.addWidget(window.button("새 설정 프로필", self.new_profile, True))
        row.addWidget(window.button("현재 경기의 관찰 구간 추가", self.add_sample))
        layout.addLayout(row)
        self.output = QTextBrowser()
        layout.addWidget(self.output, 1)
        layout.addWidget(label("자동 조준·입력 수집·게임 감도 자동 변경은 사용하지 않습니다. 영상만으로 DPI나 cm/360을 역산하지 않습니다.", "banner"))
        self.signature = None

    def refresh(self):
        profiles = self.store.profiles()
        ids = [p["id"] for p in profiles]
        if ids != self.signature:
            self.signature = ids
            self.profiles.clear()
            for p in profiles:
                self.profiles.addItem(f"{p['name']} · {p['scope']} · DPI {p['dpi']} · 감도 {p['sensitivity']}", p["id"])
        self.report()

    def report(self):
        identity = self.profiles.currentData()
        profile = next((p for p in self.store.profiles() if p["id"] == identity), None)
        samples = self.store.aim_samples(identity) if identity else []
        report = aim_report(profile, samples)
        self.output.setPlainText("판단할 자료 부족\n\n" + report["reason"] +
                                 f"\n\n사용자 확인 유효 표본: {report['valid_samples']} · 제외: {report['excluded_samples']}\n" +
                                 "\n".join(f"{k}: {v}" for k, v in report["observations"].items()) +
                                 "\n\n훈련장 비교 절차\n" + "\n".join(report["protocol"]) +
                                 "\n\n현재 프로필\n" + (json.dumps(profile, ensure_ascii=False, indent=2) if profile else "설정을 먼저 입력하세요."))

    def new_profile(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("감도 프로필 · 설정 변경 시 새로 저장")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        layout.addLayout(form)
        fields = {}
        for key, title, default in [("name", "이름", "기본 프로필"), ("dpi", "DPI", "800"),
                                     ("sensitivity", "기본 감도 (게임 설정 단위)", "1.0"),
                                     ("ads", "ADS 감도", ""), ("scope", "분석할 배율", "1x"),
                                     ("scope_sensitivity", "해당 배율 감도", ""), ("fov", "FOV", ""),
                                     ("resolution", "해상도 / 비율", "1920×1080 / 16:9"),
                                     ("device", "입력 장치", ""), ("acceleration", "마우스 가속 설정", "미확인"),
                                     ("weapon", "주 무기", ""), ("space", "마우스 이동 공간 (cm)", "")]:
            fields[key] = QLineEdit(default)
            form.addRow(title, fields[key])
        error = label("", "warning")
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        def save():
            try:
                import math
                values = {k: v.text().strip() for k, v in fields.items()}
                values["dpi"] = int(values["dpi"])
                values["sensitivity"] = float(values["sensitivity"])
                if values["dpi"] <= 0 or not math.isfinite(values["sensitivity"]) or values["sensitivity"] <= 0 or not values["scope"] or not values["name"]:
                    raise ValueError("이름·배율·양수 DPI와 감도가 필요합니다.")
                self.store.profile(values)
                dialog.accept()
                self.refresh()
            except ValueError as exc:
                error.setText(str(exc))
        buttons.accepted.connect(save)
        layout.addWidget(buttons)
        dialog.exec()

    def add_sample(self):
        match_id = self.window.current_id
        identity = self.profiles.currentData()
        if not match_id or not identity:
            self.window.error("경기 상세에서 경기를 선택하고 감도 프로필을 저장해 주세요.")
            return
        match = self.store.match(match_id)
        dialog = QDialog(self)
        dialog.setWindowTitle("수동 조준 관찰 · 자동 표적 분석 아님")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        layout.addLayout(form)
        start = seconds_box(match["info"]["duration"], self.window.player.position()/1000)
        end = seconds_box(match["info"]["duration"], min(match["info"]["duration"], start.value()+3))
        observation = QComboBox()
        observation.addItems(["오버슈트 의심", "언더슈트 의심", "좌우 보정", "미세 흔들림", "추적 오차", "판단 보류"])
        form.addRow("시작", start)
        form.addRow("종료", end)
        form.addRow("사용자 관찰", observation)
        confounders = []
        for text in ["표적 가림 / 탐지 불확실", "반동 / 피격 / 카메라 흔들림", "사용자 / 표적 이동", "설정·배율 불일치"]:
            checkbox = QCheckBox(text)
            layout.addWidget(checkbox)
            confounders.append(checkbox)
        reviewed = QCheckBox("원본과 프로필·무기·배율을 확인함")
        layout.addWidget(reviewed)
        layout.addWidget(label("60fps 미만 자료 또는 방해 요인이 있는 구간은 유효 표본에서 제외합니다.", "muted"))
        error = label("", "warning")
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        def save():
            try:
                if start.value() >= end.value():
                    raise ValueError("끝은 시작보다 뒤여야 합니다.")
                self.store.aim_sample(match_id, identity, {"start": start.value(), "end": end.value(),
                    "observation": observation.currentText(), "reviewed": reviewed.isChecked(),
                    "confounders": [c.text() for c in confounders if c.isChecked()], "fps": match["info"]["fps"]})
                dialog.accept()
                self.report()
            except ValueError as exc:
                error.setText(str(exc))
        buttons.accepted.connect(save)
        layout.addWidget(buttons)
        dialog.exec()


class HistoryPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.store = window.store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.addWidget(label("개인 기록", "heading"))
        layout.addWidget(label("검토 기록과 설정 변화를 확인합니다. 반복 실수·개선된 습관의 자동 판정은 후속 단계입니다.", "muted"))
        self.text = QTextBrowser()
        layout.addWidget(self.text)

    def refresh(self):
        matches = self.store.matches()
        goals = Counter(m["goal"] for m in matches)
        reviewed = sum(e.verified for m in matches for e in self.store.evidence(m["id"]))
        with self.store.connect() as db:
            disputes = db.execute("SELECT reason,created_at FROM disputes ORDER BY created_at DESC").fetchall()
        self.text.setPlainText(f"가져온 경기 {len(matches)}개\n확인한 근거 {reviewed}개\n감도 프로필 {len(self.store.profiles())}개\n\n경기 목표\n" +
                               "\n".join(f"{g}: {count}경기" for g, count in goals.items()) +
                               "\n\n피드백에 남긴 의견\n" + ("\n\n".join(r["reason"] for r in disputes) or "아직 기록이 없습니다.") +
                               "\n\n반복 실수와 개선 추세\n장면별 검증 자료와 여러 경기의 비교가 필요합니다. 현재 수치로 실력 변화를 추정하지 않습니다.")


class SettingsPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.store = window.store
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.addWidget(label("개인정보와 저장 설정", "heading"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(label("로컬 전용 · 외부 AI 업로드 미구현\n영상·음성·자막을 외부로 전송하지 않습니다. API 키도 필요하지 않습니다.", "banner"))
        layout.addWidget(label(f"현재 보관함\n{self.store.root}\n위치는 시작 시 --data-dir 옵션으로 선택할 수 있습니다.", "muted"))
        form = QFormLayout()
        self.model = QLineEdit(self.store.setting("model_path", ""))
        self.model.setPlaceholderText("로컬 faster-whisper / CTranslate2 모델 폴더")
        form.addRow("한국어 전사 모델", self.model)
        layout.addLayout(form)
        layout.addWidget(window.button("모델 폴더 선택", self.choose_model))
        layout.addWidget(label("model.bin · config.json · tokenizer.json이 필요합니다. 자동 모델 다운로드는 하지 않습니다.\n"
                               "모델이 없으면 SRT를 가져오거나 전사를 직접 입력·수정할 수 있습니다. 기본 실행은 CPU int8입니다.", "muted"))
        storage = QFormLayout()
        self.quota = QSpinBox()
        self.quota.setRange(1, 2000)
        self.quota.setSuffix(" GB")
        self.quota.setValue(self.store.setting("quota_gb", 50))
        self.retention = QSpinBox()
        self.retention.setRange(1, 3650)
        self.retention.setSuffix(" 일")
        self.retention.setValue(self.store.setting("retention_days", 30))
        storage.addRow("새 영상 가져오기 한도", self.quota)
        storage.addRow("보관 기간", self.retention)
        layout.addLayout(storage)
        layout.addWidget(label("보관 기간이 지난 경기는 아래 정리 버튼으로 확인 후 삭제합니다. 자동 삭제는 하지 않습니다.", "muted"))
        layout.addWidget(window.button("설정 저장", self.save, True))
        layout.addWidget(window.button("보관 기간이 지난 경기 정리", self.cleanup))
        layout.addWidget(label("실시간 수집 · 다음 단계", "heading"))
        layout.addWidget(label("화면 / 출력 장치 / 마이크 수집은 아직 연결하지 않았습니다.\n"
                               "Windows 캡처·WASAPI·동기화·게임 종료 감지는 2단계에서 구현하고 Windows에서 검증합니다.\n"
                               "현재는 경기 영상의 원본 음성 트랙을 재생·분석하며 장치 권한을 요청하지 않습니다.", "muted"))
        layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def choose_model(self):
        path = QFileDialog.getExistingDirectory(self, "로컬 음성 모델 폴더")
        if path:
            self.model.setText(path)

    def save(self):
        path = self.model.text().strip()
        if path and any(not (Path(path)/f).is_file() for f in ("model.bin", "config.json", "tokenizer.json")):
            self.window.error("모델 폴더의 model.bin, config.json, tokenizer.json을 확인해 주세요.")
            return
        self.store.set_setting("model_path", path)
        self.store.set_setting("quota_gb", self.quota.value())
        self.store.set_setting("retention_days", self.retention.value())
        self.window.statusBar().showMessage("설정을 저장했습니다.", 5000)

    def cleanup(self):
        expired = expired_matches(self.store)
        if not expired:
            self.window.error("보관 기간이 지난 경기가 없습니다.")
            return
        if self.window.tasks or any(self.window.jobs.active(m["id"]) for m in expired):
            self.window.error("분석과 가져오기·내보내기가 끝난 뒤 정리해 주세요.")
            return
        names = "\n".join(m["title"] for m in expired)
        if QMessageBox.question(self, "기간 지난 경기 삭제", f"앱 보관함의 {len(expired)}경기와 관련 기록을 삭제합니다.\n{names}\n계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        self.window.player.stop()
        self.window.player.setSource(QUrl())
        for m in expired:
            try:
                delete_match(self.store, m["id"])
                if self.window.current_id == m["id"]:
                    self.window.current_id = self.window.current_event = None
            except Exception as exc:
                self.window.error(str(exc))
                break
        self.window.switch(0)
        self.window.refresh()
