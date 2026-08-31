import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from .config import DB_PATH, ensure_dirs

_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _lock:
        conn = connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime TEXT,
                    size INTEGER NOT NULL,
                    chunk_size INTEGER NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    received TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    path TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    video_path TEXT,
                    poster_path TEXT,
                    duration REAL,
                    width INTEGER,
                    height INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    progress TEXT,
                    face_cache_path TEXT,
                    type TEXT NOT NULL DEFAULT 'public',
                    user_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    audio_name TEXT NOT NULL,
                    steps INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress TEXT,
                    output_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    progress_percent REAL,
                    current_chunk INTEGER,
                    total_chunks INTEGER,
                    remaining_seconds INTEGER,
                    estimated_seconds INTEGER,
                    audio_duration REAL,
                    stage TEXT,
                    infer_started_at TEXT,
                    tqdm_remaining REAL,
                    progress_updated_at TEXT,
                    username TEXT,
                    task_name TEXT,
                    preview_path TEXT,
                    FOREIGN KEY (character_id) REFERENCES characters(id)
                );
                """
            )
            conn.commit()
            char_cols = {row[1] for row in conn.execute("PRAGMA table_info(characters)")}
            for name, typ in (
                ("progress", "TEXT"),
                ("face_cache_path", "TEXT"),
                ("type", "TEXT"),
                ("user_id", "TEXT"),
                ("preview_path", "TEXT"),
            ):
                if name not in char_cols:
                    conn.execute(f"ALTER TABLE characters ADD COLUMN {name} {typ}")
            conn.execute("UPDATE characters SET type = 'public' WHERE type IS NULL OR type = ''")
            existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            for name, typ in (
                ("progress_percent", "REAL"),
                ("current_chunk", "INTEGER"),
                ("total_chunks", "INTEGER"),
                ("remaining_seconds", "INTEGER"),
                ("estimated_seconds", "INTEGER"),
                ("audio_duration", "REAL"),
                ("stage", "TEXT"),
                ("infer_started_at", "TEXT"),
                ("tqdm_remaining", "REAL"),
                ("progress_updated_at", "TEXT"),
                ("username", "TEXT"),
                ("task_name", "TEXT"),
                ("preview_path", "TEXT"),
            ):
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {typ}")
            conn.commit()
        finally:
            conn.close()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return dict(row)
