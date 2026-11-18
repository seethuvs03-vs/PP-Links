import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def connect(path):
    con=sqlite3.connect(path,timeout=30)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback(); raise
    finally: con.close()

def init_db(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with connect(path) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, original_url TEXT NOT NULL,
          platform TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
          category TEXT NOT NULL DEFAULT 'Uncategorized', subcategory TEXT NOT NULL DEFAULT '',
          keywords TEXT NOT NULL DEFAULT '[]', confidence REAL, reason TEXT NOT NULL DEFAULT '',
          manual_category INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
          error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aliases(url TEXT PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id));
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued', kind TEXT NOT NULL DEFAULT 'upload',
          total INTEGER NOT NULL DEFAULT 0, new INTEGER NOT NULL DEFAULT 0,
          existing INTEGER NOT NULL DEFAULT 0, updated INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '', work_total INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS job_items(
          job_id INTEGER REFERENCES jobs(id), item_id INTEGER REFERENCES items(id),
          state TEXT NOT NULL DEFAULT 'queued', PRIMARY KEY(job_id,item_id));
        CREATE TABLE IF NOT EXISTS occurrences(
          id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), item_id INTEGER REFERENCES items(id),
          sender TEXT, sent_at TEXT, timestamp_raw TEXT, message TEXT, original_url TEXT);
        CREATE TABLE IF NOT EXISTS legacy_rows(
          id INTEGER PRIMARY KEY, source TEXT, row_number INTEGER, raw_json TEXT,
          item_id INTEGER REFERENCES items(id), note TEXT);
        CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
        CREATE INDEX IF NOT EXISTS idx_occ_item ON occurrences(item_id);
        CREATE TABLE IF NOT EXISTS detail_edits(
          id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
          created_at TEXT NOT NULL, details TEXT NOT NULL);
        PRAGMA user_version=2;
        ''')
