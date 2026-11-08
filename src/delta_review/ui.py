from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from html import escape
from pathlib import Path
import json
import threading

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame,
                              QHBoxLayout, QHeaderView, QInputDialog, QListWidget, QListWidgetItem,
                              QMainWindow, QMessageBox, QPushButton, QSlider, QSplitter,
                              QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
                              QTextBrowser, QVBoxLayout, QWidget)

from .analysis import GOALS
from .domain import ReviewEvent, uid
from .media import export_clip
from .service import JobQueue, delete_match, import_media
from .storage import Store
from .transcription import parse_srt
from .ui_support import AnnotationDialog, STYLE, Task, VideoSurface, clock, label, render_review

STATUS = {None: "미분석", "queued": "대기", "running": "분석 중", "completed": "완료 · 검토 필요",
          "partial": "부분 완료 · 전사 대기", "failed": "실패 · 재시도 가능", "cancelled": "취소",
          "interrupted": "중단 · 재시도 가능"}


class MainWindow(QMainWindow):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.jobs = JobQueue(store)
        self.tasks: list[Task] = []
        self.import_cancel = threading.Event()
        self.current_id = None
        self.current_event = None
        self.last_signature = None
        self.setWindowTitle("Delta Review · 로컬 경기 복기")
        self.resize(1380, 880)
        self.setMinimumSize(1100, 740)
        self.setStyleSheet(STYLE)
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(root)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(18, 14, 18, 20)
        nav.addWidget(label("Δ  DELTA\n    REVIEW", "brand"))
        nav.addWidget(label("기록하고, 되짚고, 개선하기", "muted"))
        nav.addSpacing(22)
        self.pages = QStackedWidget()
        self.nav_buttons = []
        for i, name in enumerate(["01   경기 보관함", "02   경기 상세", "03   감도 관찰", "04   개인 기록", "05   설정·저장"]):
            button = QPushButton(name)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, index=i: self.switch(index))
            nav.addWidget(button)
            self.nav_buttons.append(button)
        nav.addStretch()
        nav.addWidget(label("●  로컬 전용\n외부 전송 0 B\n\nv0.1 · 녹화 파일 복기", "muted"))
        outer.addWidget(sidebar)
        outer.addWidget(self.pages, 1)
        self.create_library()
        self.create_detail()
        from .ui_panels import AimPanel, HistoryPanel, SettingsPanel
        self.aim = AimPanel(self)
        self.history = HistoryPanel(self)
        self.settings = SettingsPanel(self)
        self.pages.addWidget(self.aim)
        self.pages.addWidget(self.history)
        self.pages.addWidget(self.settings)
        self.switch(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(700)
        self.refresh()

    def page(self, title, subtitle):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(16)
        layout.addWidget(label(title, "heading"))
        layout.addWidget(label(subtitle, "muted"))
        self.pages.addWidget(widget)
        return layout

    def switch(self, index):
        self.pages.setCurrentIndex(index)
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == index)
        if hasattr(self, "history"):
            self.history.refresh()
            self.aim.refresh()

    def button(self, text, callback, primary=False):
        b = QPushButton(text)
        b.clicked.connect(callback)
        if primary:
            b.setObjectName("primary")
        return b

    def create_library(self):
        layout = self.page("한 판의 기록, 다음 판의 판단으로.", "MATCH LIBRARY   /   그 순간 보고 들을 수 있었던 정보부터 복기합니다.")
        layout.addWidget(label("시작하기   ① 녹화를 마친 영상 가져오기   →   ② 전사·사건 확인   →   ③ 근거를 재생하며 복기\n"
                               "현재 버전은 파일 기반입니다. 경기 중 화면·소리 녹화와 실시간 전술 조언은 제공하지 않습니다.", "banner"))
        stats = QHBoxLayout()
        self.count_label = label("0\n저장한 경기", "stat")
        self.done_label = label("0\n생성된 보고서", "stat")
        self.disk_label = label("0 MB\n보관된 원본", "stat")
        for w in (self.count_label, self.done_label, self.disk_label):
            stats.addWidget(w)
        layout.addLayout(stats)
        actions = QHBoxLayout()
        actions.addWidget(label("이번 경기의 목표"))
        self.goal = QComboBox()
        self.goal.addItems(GOALS)
        actions.addWidget(self.goal)
        actions.addStretch()
        self.import_button = self.button("＋ 녹화 파일 가져오기", self.choose_import, True)
        actions.addWidget(self.import_button)
        layout.addLayout(actions)
        self.import_status = label("원본 파일은 그대로 두고 앱 보관함에 복사합니다. MP4 · MKV · MOV · WebM", "muted")
        layout.addWidget(self.import_status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["경기 / 녹화 이름", "가져온 날짜", "길이", "결과", "분석 상태"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(lambda r, c: self.open_match(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.table, 1)
        layout.addWidget(self.button("선택한 경기 열기", self.open_selected))

    def create_detail(self):
        layout = self.page("경기 상세", "복기할 경기를 보관함에서 선택하세요. 사건을 누르면 원본 구간으로 이동합니다.")
        self.detail_title = label("선택된 경기 없음", "banner")
        layout.addWidget(self.detail_title)
        toolbar = QHBoxLayout()
        for title, callback in [("분석 / 재시도", self.analyze_current), ("취소", self.cancel_current),
                                ("수동 사건 추가", self.add_annotation), ("SRT 가져오기", self.import_srt),
                                ("근거 클립 저장", self.save_clip), ("경기 삭제", self.delete_current)]:
            toolbar.addWidget(self.button(title, callback))
        layout.addLayout(toolbar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.video = VideoSurface()
        self.video.setMinimumHeight(220)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.video.videoSink())
        self.player.errorOccurred.connect(lambda error, message: self.detail_status.setText("재생 오류: " + message))
        left_layout.addWidget(self.video, 3)
        self.subtitle = label("자막 없음", "banner")
        self.subtitle.setMinimumHeight(62)
        left_layout.addWidget(self.subtitle)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.positionChanged.connect(self.position_changed)
        left_layout.addWidget(self.slider)
        controls = QHBoxLayout()
        controls.addWidget(self.button("재생 / 일시정지", self.toggle_play))
        self.time_label = label("00:00.00", "muted")
        controls.addWidget(self.time_label)
        self.audio_track = QComboBox()
        self.audio_track.currentIndexChanged.connect(self.track_changed)
        controls.addWidget(self.audio_track, 1)
        left_layout.addLayout(controls)
        left_layout.addWidget(label("사건 타임라인 · 더블클릭하여 해당 근거 수정", "muted"))
        self.events_list = QListWidget()
        self.events_list.currentItemChanged.connect(self.event_selected)
        self.events_list.itemDoubleClicked.connect(self.edit_selected_evidence)
        left_layout.addWidget(self.events_list, 2)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        tabs = QTabWidget()
        self.feedback = QTextBrowser()
        self.feedback.document().setDefaultStyleSheet("a { color: #b5f1c4; } small { color: #9caeb2; }")
        self.feedback.setOpenLinks(False)
        self.feedback.anchorClicked.connect(self.evidence_link)
        tabs.addTab(self.feedback, "근거와 피드백")
        evidence_page = QWidget()
        evidence_layout = QVBoxLayout(evidence_page)
        self.evidence_list = QListWidget()
        self.evidence_list.itemDoubleClicked.connect(self.edit_evidence_item)
        evidence_layout.addWidget(self.evidence_list)
        evidence_layout.addWidget(self.button("선택한 근거·전사 수정", self.edit_evidence_item))
        tabs.addTab(evidence_page, "전사·근거 수정")
        self.metadata = QTextBrowser()
        tabs.addTab(self.metadata, "분석 범위")
        right_layout.addWidget(tabs)
        right_layout.addWidget(self.button("이 피드백에 대한 의견 남기기", self.leave_feedback))
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([540, 510])
        layout.addWidget(splitter, 1)
        self.detail_status = label("", "warning")
        layout.addWidget(self.detail_status)

    def error(self, message):
        QMessageBox.warning(self, "Delta Review", str(message))

    def run_task(self, function, success):
        task = Task(function, self)
        self.tasks.append(task)
        task.succeeded.connect(success)
        task.failed.connect(self.error)
        task.finished.connect(lambda: self.tasks.remove(task))
        task.start()
        return task

    def choose_import(self):
        name, _ = QFileDialog.getOpenFileName(self, "녹화 파일 가져오기", "", "영상 (*.mp4 *.mkv *.mov *.webm)")
        if not name:
            return
        self.import_button.setEnabled(False)
        self.import_status.setText("원본 정보를 확인하고 안전하게 복사하는 중…")
        goal = self.goal.currentText()
        self.import_cancel.clear()
        task = self.run_task(lambda: import_media(self.store, Path(name), goal, cancel=self.import_cancel), self.imported)
        task.finished.connect(lambda: self.import_button.setEnabled(True))
        task.failed.connect(lambda: self.import_status.setText("가져오기 실패 · 원본은 변경되지 않았습니다."))

    def imported(self, match_id):
        self.import_status.setText("가져오기 완료 · 원본을 보존하고 분석을 등록했습니다.")
        self.jobs.submit(match_id)
        self.refresh()
        self.open_match(match_id)

    def open_selected(self):
        row = self.table.currentRow()
        if row >= 0:
            self.open_match(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def refresh(self):
        rows = self.store.matches()
        signature = [(r["id"], r["state"], r["progress"], r["revision"], r["report_revision"]) for r in rows]
        if signature == self.last_signature:
            return
        self.last_signature = signature
        selected = self.table.currentRow()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            info = json.loads(row["info"])
            values = [("[데모] " if row["demo"] else "")+row["title"],
                      datetime.fromisoformat(row["created_at"]).astimezone().strftime("%m.%d %H:%M"),
                      clock(info["duration"]), "미확인", STATUS.get(row["state"], row["state"]) +
                      (f" {row['progress']}%" if row["state"] == "running" else "")]
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
                self.table.setItem(i, j, item)
            self.table.setRowHeight(i, 55)
        if selected >= 0 and selected < len(rows):
            self.table.selectRow(selected)
        self.count_label.setText(f"{len(rows)}\n저장한 경기")
        self.done_label.setText(f"{sum(bool(r['report']) for r in rows)}\n생성된 보고서")
        self.disk_label.setText(f"{sum(json.loads(r['info'])['size'] for r in rows)/1024**2:.1f} MB\n보관된 원본")
        if self.current_id:
            self.load_detail()

    def open_match(self, match_id):
        self.current_id = match_id
        self.current_event = None
        match = self.store.match(match_id)
        self.player.setSource(QUrl.fromLocalFile(match["media"]))
        self.audio_track.blockSignals(True)
        self.audio_track.clear()
        for track in match["info"]["tracks"]:
            if track["type"] == "audio":
                self.audio_track.addItem(f"음성 #{track['index']} · {track['channels']}ch · {track['language']}", track["index"])
        self.audio_track.setCurrentIndex(max(0, self.audio_track.findData(self.store.setting(f"audio_track:{match_id}"))))
        self.audio_track.blockSignals(False)
        self.load_detail()
        self.switch(1)

    def load_detail(self):
        match = self.store.match(self.current_id)
        self.detail_title.setText(("[테스트 데모] " if match["demo"] else "") + match["title"] +
                                 f"   ·   {clock(match['info']['duration'])}   ·   목표: {match['goal']}")
        selected = self.current_event
        self.events_list.blockSignals(True)
        self.events_list.clear()
        events = self.store.events(self.current_id)
        for event in events:
            item = QListWidgetItem(f"{clock(event.decision_at)}   {event.title}")
            item.setData(Qt.ItemDataRole.UserRole, event.id)
            self.events_list.addItem(item)
            if event.id == selected:
                self.events_list.setCurrentItem(item)
        self.events_list.blockSignals(False)
        self.evidence_cache = self.store.evidence(self.current_id)
        self.evidence_list.clear()
        for e in self.evidence_cache:
            item = QListWidgetItem(f"{clock(e.start)}  {'확인' if e.verified else '미확인'}  {e.text[:85]}")
            item.setData(Qt.ItemDataRole.UserRole, e.id)
            self.evidence_list.addItem(item)
        report = match["report"]
        if match["report_stale"]:
            self.feedback.setPlainText("근거가 추가·수정되었거나 아직 보고서가 없습니다. ‘분석 / 재시도’를 누르면 현재 근거로 보고서를 생성합니다.")
        elif report:
            self.feedback.setPlainText(report["summary"] + "\n\n잘한 판단\n" + report["strengths"] +
                                       "\n\n우선 검토할 순간 (상위 3개)\n" + "\n".join(r["title"] for r in report["reviews"] if r["id"] in report["priorities"]) +
                                       "\n\n" + report["transcription"] + "\n\n" + "\n".join(report["limitations"]))
        self.metadata.setPlainText(json.dumps({"원본": match["info"], "실측": report["metrics"] if report else "미분석",
                                              "전사": report["transcription"] if report else "미분석"}, ensure_ascii=False, indent=2))
        row = next(r for r in self.store.matches() if r["id"] == self.current_id)
        self.detail_status.setText(STATUS.get(row["state"], "미분석") + "  ·  " + (row["message"] or ""))
        if selected:
            self.show_review(selected, seek=False)

    def show_review(self, event_id, seek=True):
        if not self.current_id:
            return
        match = self.store.match(self.current_id)
        event = next((e for e in self.store.events(self.current_id) if e.id == event_id), None)
        if not event:
            return
        self.current_event = event_id
        if seek:
            self.player.setPosition(int(event.start*1000))
            self.player.play()
        if match["report"] and not match["report_stale"]:
            review = next((r for r in match["report"]["reviews"] if r["id"] == event_id), None)
            if review:
                self.feedback.setHtml(render_review(review))

    def event_selected(self, current, previous=None):
        if current:
            self.show_review(current.data(Qt.ItemDataRole.UserRole))

    def evidence_link(self, url):
        if url.scheme() != "evidence":
            return
        evidence = next((e for e in self.evidence_cache if e.id == url.path()), None)
        if evidence:
            self.player.setPosition(int(evidence.start*1000))
            self.player.play()

    def position_changed(self, value):
        if not self.slider.isSliderDown():
            self.slider.setValue(value)
        self.time_label.setText(clock(value/1000))
        text = [e.text for e in getattr(self, "evidence_cache", []) if e.kind == "transcript" and e.start <= value/1000 <= e.end]
        self.subtitle.setText(" / ".join(text) if text else "자막 없음")

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def track_changed(self, index):
        if self.current_id and index >= 0:
            self.store.set_setting(f"audio_track:{self.current_id}", self.audio_track.itemData(index))
            self.player.setActiveAudioTrack(index)

    def analyze_current(self):
        if self.current_id:
            self.jobs.submit(self.current_id)

    def cancel_current(self):
        if self.current_id:
            self.jobs.cancel(self.current_id)

    def editable(self):
        if not self.current_id:
            return False
        if self.jobs.active(self.current_id):
            self.error("분석을 취소하고 작업이 멈춘 뒤 수정해 주세요.")
            return False
        return True

    def add_annotation(self, checked=False, evidence=None):
        if not self.editable():
            return
        match = self.store.match(self.current_id)
        dialog = AnnotationDialog(match["info"]["duration"], self.player.position()/1000, evidence,
                                  self.evidence_cache, self)
        if evidence:
            previous_event = next((ev for ev in self.store.events(self.current_id) if evidence.id in ev.evidence_ids), None)
            if previous_event:
                dialog.action.setText(previous_event.action)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                e, event = dialog.values()
                if e.supersedes:
                    old = next(x for x in self.evidence_cache if x.id == e.supersedes)
                    if old.available_at >= e.available_at:
                        raise ValueError("정정 발화는 이전 발화보다 나중이어야 합니다.")
                if evidence:
                    linked = [ev for ev in self.store.events(self.current_id) if e.id in ev.evidence_ids]
                    event = replace(event, id=linked[0].id) if linked else event
                self.store.save_annotation(self.current_id, e, event)
                self.load_detail()
            except ValueError as exc:
                self.error(str(exc))

    def edit_evidence_item(self, item=None):
        if not isinstance(item, QListWidgetItem):
            item = self.evidence_list.currentItem()
        if item:
            e = next(e for e in self.evidence_cache if e.id == item.data(Qt.ItemDataRole.UserRole))
            self.add_annotation(evidence=e)

    def edit_selected_evidence(self, item):
        if not self.current_id:
            return
        event = next(e for e in self.store.events(self.current_id) if e.id == item.data(Qt.ItemDataRole.UserRole))
        if event.evidence_ids:
            evidence = next(e for e in self.evidence_cache if e.id == event.evidence_ids[0])
            self.add_annotation(evidence=evidence)

    def import_srt(self):
        if not self.editable():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "UTF-8 SRT 자막 가져오기", "", "SubRip (*.srt)")
        if filename:
            try:
                duration = self.store.match(self.current_id)["info"]["duration"]
                items = parse_srt(Path(filename).read_text(encoding="utf-8-sig"), duration)
                annotations = []
                for e in items:
                    event = ReviewEvent(uid(), e.start, min(duration, e.end+3), e.end, "발화 · 내용 확인", evidence_ids=[e.id])
                    annotations.append((e, event))
                self.store.save_annotations(self.current_id, annotations)
                self.load_detail()
            except Exception as exc:
                self.error(str(exc))

    def save_clip(self):
        if not self.current_event or not self.current_id:
            self.error("타임라인에서 사건을 선택해 주세요.")
            return
        match = self.store.match(self.current_id)
        event = next(e for e in self.store.events(self.current_id) if e.id == self.current_event)
        if QMessageBox.question(self, "클립의 음성 확인", "클립에 선택 구간의 모든 음성 트랙(아군·본인 음성 포함 가능)이 저장됩니다. 계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "로컬 클립 저장", "review-clip.mp4", "MP4 (*.mp4)")
        if filename:
            target = Path(filename).with_suffix(".mp4")
            self.run_task(lambda: export_clip(Path(match["media"]), target, event.start, event.end, match["info"]["duration"]),
                          lambda _: self.detail_status.setText("클립을 로컬에 저장했습니다. 앱 밖으로 내보낸 사본은 별도로 관리하세요."))

    def leave_feedback(self):
        if self.current_id and self.current_event:
            text, ok = QInputDialog.getMultiLineText(self, "피드백 의견", "동의하지 않는 이유 또는 추가 맥락")
            if ok and text.strip():
                self.store.dispute(self.current_id, self.current_event, text)
                self.detail_status.setText("의견을 로컬에 저장했습니다.")

    def delete_current(self):
        if not self.editable() or self.tasks:
            return
        if QMessageBox.question(self, "경기 삭제", "앱이 보관한 원본 사본·전사·사건·분석·의견을 삭제합니다. 가져오기 전 원본과 내보낸 사본은 유지됩니다. 삭제할까요?") != QMessageBox.StandardButton.Yes:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        try:
            delete_match(self.store, self.current_id)
        except Exception as exc:
            self.error(str(exc))
            return
        self.current_id = self.current_event = None
        self.evidence_cache = []
        self.events_list.clear()
        self.evidence_list.clear()
        self.feedback.clear()
        self.metadata.clear()
        self.detail_title.setText("선택된 경기 없음")
        self.switch(0)
        self.refresh()

    def closeEvent(self, event):
        if self.tasks or self.jobs.cancels:
            self.import_cancel.set()
            for identity in list(self.jobs.cancels):
                self.jobs.cancel(identity)
            self.statusBar().showMessage("작업 취소를 요청했습니다. 작업이 멈춘 뒤 다시 닫아 주세요. 원본은 보존됩니다.")
            event.ignore()
            return
        self.jobs.close()
        self.player.stop()
        event.accept()
