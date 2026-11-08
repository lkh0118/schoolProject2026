from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .domain import Evidence, ReviewEvent, now, span, uid


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "delta-review.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    media TEXT NOT NULL, info TEXT NOT NULL, goal TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, report TEXT, report_revision INTEGER,
                    demo INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    match_id TEXT PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
                    state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aim_samples (
                    id TEXT PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    profile_id TEXT NOT NULL REFERENCES profiles(id), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS disputes (
                    id TEXT PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def add_match(self, match_id, title, media, info, goal, demo=False):
        with self.connect() as db:
            db.execute("INSERT INTO matches(id,title,created_at,media,info,goal,demo) VALUES(?,?,?,?,?,?,?)",
                       (match_id, title, now(), str(media), json.dumps(info), goal, int(demo)))

    def matches(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT m.*,j.state,j.progress,j.message FROM matches m LEFT JOIN jobs j ON m.id=j.match_id ORDER BY created_at DESC")]

    def match(self, match_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if row is None:
            raise ValueError("경기를 찾을 수 없습니다.")
        data = dict(row)
        data["info"] = json.loads(data["info"])
        data["report"] = json.loads(data["report"]) if data["report"] else None
        data["report_stale"] = data["report_revision"] != data["revision"]
        return data

    def evidence(self, match_id) -> list[Evidence]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM evidence WHERE match_id=?", (match_id,)).fetchall()
        return sorted([Evidence(**json.loads(r[0])) for r in rows], key=lambda e: (e.start, e.id))

    def events(self, match_id) -> list[ReviewEvent]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM events WHERE match_id=?", (match_id,)).fetchall()
        return sorted([ReviewEvent(**json.loads(r[0])) for r in rows], key=lambda e: (e.start, e.id))

    def save_annotation(self, match_id, evidence: Evidence, event: ReviewEvent | None = None):
        self.save_annotations(match_id, [(evidence, event)])

    def save_annotations(self, match_id, annotations):
        """Atomic batch: a failed SRT/ASR save cannot masquerade as a complete transcript."""
        from dataclasses import asdict
        annotations = list(annotations)
        if not annotations:
            return
        duration = self.match(match_id)["info"]["duration"]
        for evidence, event in annotations:
            evidence.validate(duration)
            if event:
                event.validate(duration)
        with self.connect() as db:
            for evidence, event in annotations:
                for table, item in (("evidence", evidence), ("events", event)):
                    if item is None:
                        continue
                    existing = db.execute(f"SELECT match_id FROM {table} WHERE id=?", (item.id,)).fetchone()
                    if existing and existing[0] != match_id:
                        raise ValueError("다른 경기의 ID를 사용할 수 없습니다.")
                    db.execute(f"INSERT INTO {table}(id,match_id,body) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                               (item.id, match_id, json.dumps(asdict(item), ensure_ascii=False)))
            db.execute("UPDATE matches SET revision=revision+1 WHERE id=?", (match_id,))

    def save_report(self, match_id, report, revision):
        with self.connect() as db:
            row = db.execute("SELECT revision FROM matches WHERE id=?", (match_id,)).fetchone()
            if not row or row[0] != revision:
                raise ValueError("분석 중 근거가 수정되었습니다. 다시 분석해 주세요.")
            db.execute("UPDATE matches SET report=?,report_revision=? WHERE id=?",
                       (json.dumps(report, ensure_ascii=False), revision, match_id))

    def enqueue(self, match_id) -> bool:
        with self.connect() as db:
            result = db.execute("""INSERT INTO jobs VALUES(?, 'queued',0,'대기 중',?)
                ON CONFLICT(match_id) DO UPDATE SET state='queued',progress=0,message='대기 중',updated_at=excluded.updated_at
                WHERE jobs.state NOT IN ('queued','running')""", (match_id, now()))
            return result.rowcount == 1

    def job(self, match_id, state, progress=0, message=""):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=?,progress=?,message=?,updated_at=? WHERE match_id=?",
                       (state, progress, message, now(), match_id))

    def recover_jobs(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='interrupted',message='이전 실행이 중단됨 · 원본은 보존됨' WHERE state IN ('queued','running')")

    def profile(self, body):
        identity = uid()
        with self.connect() as db:
            db.execute("INSERT INTO profiles VALUES(?,?,?)", (identity, now(), json.dumps(body, ensure_ascii=False)))
        return identity

    def profiles(self):
        with self.connect() as db:
            return [{"id": r["id"], **json.loads(r["body"])} for r in db.execute("SELECT * FROM profiles ORDER BY created_at DESC")]

    def aim_sample(self, match_id, profile_id, body):
        duration = self.match(match_id)["info"]["duration"]
        span(body["start"], body["end"], duration)
        with self.connect() as db:
            db.execute("INSERT INTO aim_samples VALUES(?,?,?,?)", (uid(), match_id, profile_id, json.dumps(body, ensure_ascii=False)))

    def aim_samples(self, profile_id):
        with self.connect() as db:
            return [dict(json.loads(r["body"]), match_id=r["match_id"]) for r in db.execute("SELECT * FROM aim_samples WHERE profile_id=?", (profile_id,))]

    def dispute(self, match_id, event_id, reason):
        if not reason.strip():
            raise ValueError("의견을 입력해 주세요.")
        with self.connect() as db:
            db.execute("INSERT INTO disputes VALUES(?,?,?,?,?)", (uid(), match_id, event_id, reason, now()))
