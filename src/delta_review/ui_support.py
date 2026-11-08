from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFormLayout, QLabel, QLineEdit, QTextEdit, QVBoxLayout, QWidget)

from .domain import Evidence, ReviewEvent, now, uid


def clock(seconds: float) -> str:
    return f"{int(seconds)//60:02d}:{seconds%60:05.2f}"


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def seconds_box(maximum, value=0):
    box = QDoubleSpinBox()
    box.setDecimals(3)
    box.setRange(0, int(maximum * 1000) / 1000)
    box.setSuffix(" 초")
    box.setValue(value)
    return box


class VideoSurface(QWidget):
    """Render real decoded frames in a widget, also usable in headless QA captures."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink = QVideoSink(self)
        self.frame = QImage()
        self._sink.videoFrameChanged.connect(self.on_frame)

    def videoSink(self):
        return self._sink

    def on_frame(self, frame):
        self.frame = frame.toImage() if frame.isValid() else QImage()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if not self.frame.isNull():
            size = self.frame.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x, y = (self.width()-size.width())//2, (self.height()-size.height())//2
            painter.drawImage(x, y, self.frame.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))


class Task(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.succeeded.emit(self.function())
        except Exception as exc:
            self.failed.emit(str(exc))


class AnnotationDialog(QDialog):
    def __init__(self, duration, position, evidence=None, others=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("근거·전사 수정" if evidence else "수동 사건 / 전사 추가")
        self.resize(580, 620)
        self.original = evidence
        self.duration = duration
        layout = QVBoxLayout(self)
        layout.addWidget(label("원본에서 확인한 내용만 기록하세요. 전사 확인은 발화 내용이 실제 사실이라는 뜻이 아닙니다.", "muted"))
        form = QFormLayout()
        layout.addLayout(form)
        self.kind = QComboBox()
        self.kind.addItem("직접 관찰 / 수동 사건", "manual")
        self.kind.addItem("음성 전사", "transcript")
        self.kind.addItem("화면 변화 후보", "visual_candidate")
        if evidence:
            self.kind.setCurrentIndex(self.kind.findData(evidence.kind))
        self.start = seconds_box(duration, evidence.start if evidence else position)
        self.end = seconds_box(duration, evidence.end if evidence else min(duration, position+2))
        self.cue = seconds_box(duration, evidence.cue_at if evidence else position)
        self.available = seconds_box(duration, evidence.available_at if evidence else min(duration, position+2))
        self.content = QTextEdit()
        self.content.setAcceptRichText(False)
        self.content.setPlainText(evidence.text if evidence else "")
        self.content.setPlaceholderText("예: 화면 오른쪽의 핑을 확인함 / 들린 발화를 그대로 기록")
        self.speaker = QComboBox()
        self.speaker.addItems(["미확인", "사용자", "아군(이름 미확인)"])
        if evidence:
            self.speaker.setCurrentText(evidence.speaker)
        self.action = QLineEdit()
        self.action.setPlaceholderText("직접 확인한 사용자 행동 · 미확인이면 비워 두기")
        self.verified = QCheckBox("원본을 재생하여 이 기록의 내용과 시각을 확인했음")
        self.verified.setChecked(bool(evidence and evidence.verified))
        self.supersedes = QComboBox()
        self.supersedes.addItem("없음", None)
        for old in others:
            if old.kind == "transcript" and (not evidence or old.id != evidence.id):
                self.supersedes.addItem(f"{clock(old.start)} {old.text[:50]}", old.id)
        if evidence and evidence.supersedes:
            self.supersedes.setCurrentIndex(self.supersedes.findData(evidence.supersedes))
        for title, widget in [("분류", self.kind), ("사건 시작", self.start), ("사건 종료", self.end),
                              ("단서 출현", self.cue), ("정보 이용 가능 / 판단 시점", self.available),
                              ("내용", self.content), ("화자", self.speaker), ("정정하는 이전 발화", self.supersedes),
                              ("사용자 행동", self.action)]:
            form.addRow(title, widget)
        layout.addWidget(self.verified)
        self.error = label("", "warning")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        identity = self.original.id if self.original else uid()
        evidence = Evidence(identity, self.kind.currentData(), self.start.value(), self.end.value(),
                            self.cue.value(), self.available.value(), self.content.toPlainText().strip(),
                            "user", self.verified.isChecked(), self.speaker.currentText(), now(), self.supersedes.currentData())
        event = ReviewEvent(identity + "-manual-event", evidence.start, min(self.duration, max(evidence.end, evidence.available_at)+3),
                            evidence.available_at, evidence.text[:50], self.action.text().strip() or "사용자 행동 미확인", [identity])
        return evidence, event

    def validate(self):
        try:
            e, event = self.values()
            e.validate(self.duration)
            event.validate(self.duration)
            if e.supersedes and e.kind != "transcript":
                raise ValueError("이전 발화 정정은 전사에서만 지정해 주세요.")
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


def render_review(review):
    e = escape
    sections = [f"<h2>{e(review['title'])}</h2>",
                f"<p>{clock(review['start'])}–{clock(review['end'])} · 판단 {clock(review['decision_at'])} · 확신 {e(review['confidence'])}</p>"]
    for title, key in [("직접 기록·화면 후보", "facts"), ("발화 내용 · 사실 여부와 청취 여부는 별도", "briefings")]:
        sections.append(f"<h3>{title}</h3>")
        for fact in review[key]:
            marker = "사용자 확인" if fact["verified"] else "미확인 · 판단 보류"
            sections.append(f"<p><a href='evidence:{e(fact['id'])}'>{clock(fact['start'])}–{clock(fact['end'])}</a> "
                            f"{e(fact['text'])}<br><small>{marker} · 이용 가능 {clock(fact['available_at'])} · "
                            f"근거 {e(fact['id'])} · 처리 {e(fact['processed_at'])}</small></p>")
        if not review[key]:
            sections.append("<p>이 시점까지 확인된 근거 없음</p>")
    sections.append(f"<h3>사용자 행동 기록</h3><p>{e(review['action'])}</p><h3>추론·조건부 대안</h3><p>{e(review['inference'])}</p>")
    for option in review["alternatives"]:
        sections.append(f"<p><b>{e(option['action'])}</b><br>전제: {e(option['premise'])}<br>기대 이점: {e(option['benefit'])}"
                        f"<br>위험·비용: {e(option['risk'])}<br>다른 선택 조건: {e(option['switch_when'])}</p>")
    if not review["alternatives"]:
        sections.append("<p>검토된 근거가 부족하여 전술 대안 생성을 보류했습니다.</p>")
    sections.append("<h3>확인할 수 없는 부분</h3>" + "".join(f"<p>• {e(x)}</p>" for x in review["unknowns"]))
    sections.append(f"<h3>다음 복기에서 할 일</h3><p>{e(review['next_action'])}</p>")
    if review["after_decision"]:
        sections.append("<h3>판단 이후의 정보 · 위 대안 생성에 사용하지 않음</h3>")
        for fact in review["after_decision"]:
            sections.append(f"<p>{clock(fact['start'])} {e(fact['text'])}</p>")
    return "".join(sections)


STYLE = """
QWidget { background: #11181d; color: #e6eeed; font-family: 'Noto Sans CJK KR', 'Malgun Gothic', sans-serif; font-size: 13px; }
QMainWindow { background: #11181d; }
QFrame#sidebar { background: #0b1115; border-right: 1px solid #263238; }
QFrame#sidebar QLabel, QFrame#sidebar QPushButton { background: transparent; }
QLabel#brand { font-size: 22px; font-weight: 800; color: #b5f1c4; padding: 20px 0; }
QLabel#heading { font-size: 28px; font-weight: 700; padding: 8px 0; }
QLabel#muted { color: #94a5aa; }
QLabel#warning { color: #efbd79; }
QLabel#banner { background: #1d3029; color: #c2e5cf; padding: 18px; border-radius: 8px; }
QLabel#stat { background: #1b262d; color: #b5f1c4; padding: 20px; border: 1px solid #2a3b41; border-radius: 8px; font-size: 17px; }
QPushButton { background: #25343b; border: 1px solid #35484f; border-radius: 6px; padding: 9px 14px; }
QPushButton:hover { background: #354b50; }
QPushButton:checked { background: #1d382d; color: #b5f1c4; border-color: #648c73; }
QPushButton:disabled { color: #607078; }
QPushButton#primary { background: #b5f1c4; color: #12241b; border: none; font-weight: 700; }
QLineEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox { background: #192329; border: 1px solid #34454b; border-radius: 5px; padding: 7px; selection-background-color: #3b6252; }
QTextBrowser { padding: 14px; }
QTableWidget, QListWidget { background: #151f25; alternate-background-color: #19262d; border: 1px solid #2b3b42; border-radius: 7px; }
QTableWidget::item, QListWidget::item { padding: 10px; }
QTableWidget::item:selected, QListWidget::item:selected { background: #2d4c40; color: #eaffec; }
QHeaderView::section { background: #1b2b32; border: 0; padding: 10px; color: #a8bec2; }
QTabBar::tab { background: #1b2930; padding: 10px 18px; }
QTabBar::tab:selected { color: #b5f1c4; border-bottom: 2px solid #b5f1c4; }
QTabWidget::pane { border: 1px solid #2a3a41; }
QSlider::groove:horizontal { background: #35454c; height: 5px; }
QSlider::handle:horizontal { background: #b5f1c4; width: 13px; margin: -4px 0; border-radius: 6px; }
QProgressBar { border: 1px solid #34454b; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #518f6e; }
QScrollBar:vertical { background: #152027; width: 10px; }
QScrollBar::handle:vertical { background: #41585c; min-height: 24px; }
QSplitter::handle { background: #2b3b42; }
"""
