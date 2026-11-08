import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from delta_review.domain import Evidence, ReviewEvent
from delta_review.service import analyse, import_media
from delta_review.ui import MainWindow
from delta_review.ui_support import AnnotationDialog, render_review


def test_desktop_imported_match_playback_annotation_and_screenshot(store, video, tmp_path):
    app = QApplication.instance() or QApplication([])
    identity = import_media(store, video, demo=True)
    e = Evidence("subtitle", "transcript", 0, 3, 0, 3, "[합성 테스트 자막 · 실제 브리핑 아님]", "user", True)
    event = ReviewEvent("subtitle-event", 0, 4, 3, "테스트 전사", evidence_ids=[e.id])
    store.save_annotation(identity, e, event)
    analyse(store, identity, threading.Event(), lambda *a: None)
    window = MainWindow(store)
    try:
        window.show()
        QTest.qWait(100)
        assert window.table.rowCount() == 1
        assert "[데모]" in window.table.item(0, 0).text()
        window.table.selectRow(0)
        window.open_selected()
        assert window.current_id == identity
        assert window.events_list.count() >= 2
        frames = []
        window.video.videoSink().videoFrameChanged.connect(lambda f: frames.append(f.isValid()))
        window.show_review("subtitle-event")
        deadline = time.monotonic() + 8
        while not any(frames) and time.monotonic() < deadline:
            QTest.qWait(50)
        assert any(frames), window.player.errorString()
        assert not window.video.frame.isNull()
        QTest.qWait(300)
        assert window.player.position() > 0
        assert "테스트 자막" in window.subtitle.text()
        assert "조건부" in window.feedback.toPlainText()
        window.player.pause()
        # Validate an actual editor interaction, including unsafe markup as literal text.
        editor = AnnotationDialog(4, 1, e, window.evidence_cache, window)
        editor.content.setPlainText("수정한 전사 <script>no execution</script>")
        editor.verified.setChecked(False)
        updated, _ = editor.values()
        store.save_annotation(identity, updated)
        window.load_detail()
        assert "추가·수정" in window.feedback.toPlainText()
        window.switch(2)
        assert "판단할 자료 부족" in window.aim.output.toPlainText()
        window.switch(4)
        assert any("외부 AI 업로드 미구현" in item.text() for item in window.settings.findChildren(QLabel))
        window.switch(0)
        QTest.qWait(100)
        assert window.grab().save(str(tmp_path / "desktop-library.png"))
    finally:
        window.close()
        window.jobs.thread.join(2)
        app.processEvents()


def test_html_transcript_is_escaped():
    from delta_review.analysis import build_report
    e = Evidence("literal", "transcript", 0, 1, 0, 1, "<script>bad()</script>", "srt", True)
    event = ReviewEvent("event", 0, 3, 1, "<img src='https://external.invalid/'>", evidence_ids=[e.id])
    report = build_report({"duration": 4}, [e], [event], "생존·탈출", "test", {})
    html = render_review(report["reviews"][0])
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img src=" not in html
