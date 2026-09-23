"""SQLite persistence for Smart File Manager Pro (standard library only).

Tables
------
meta(key PK, value)                               schema version + one-time migration flags
undo_batches(id PK, label, created_at, status)    one row per undoable action (status: active/undone/stale)
undo_moves(id PK, batch_id FK -> undo_batches, position, src, dst)
activity_log(id PK, ts, message)
rules(id PK, position, pattern, folder)           custom organizer rules (ordered)
hash_cache(path, kind, size, mtime, hash, updated) PK(path, kind)  speeds up the Duplicate Finder
scan_history(id PK, ts, kind, root, scanned, groups, dup_files, wasted_bytes,
             cache_hits, cache_misses, seconds)
"""
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
UNDO_LIMIT = 20
LOG_KEEP = 5000
CACHE_MAX_AGE_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS undo_batches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'undone', 'stale'))
);
CREATE TABLE IF NOT EXISTS undo_moves (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES undo_batches(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    src      TEXT    NOT NULL,
    dst      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_undo_moves_batch ON undo_moves(batch_id);
CREATE TABLE IF NOT EXISTS activity_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    position INTEGER NOT NULL,
    pattern  TEXT    NOT NULL,
    folder   TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS hash_cache (
    path    TEXT    NOT NULL,
    kind    TEXT    NOT NULL CHECK (kind IN ('head', 'full')),
    size    INTEGER NOT NULL,
    mtime   REAL    NOT NULL,
    hash    TEXT    NOT NULL,
    updated REAL    NOT NULL,
    PRIMARY KEY (path, kind)
);
CREATE TABLE IF NOT EXISTS scan_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    root         TEXT    NOT NULL,
    scanned      INTEGER NOT NULL DEFAULT 0,
    groups       INTEGER NOT NULL DEFAULT 0,
    dup_files    INTEGER NOT NULL DEFAULT 0,
    wasted_bytes INTEGER NOT NULL DEFAULT 0,
    cache_hits   INTEGER NOT NULL DEFAULT 0,
    cache_misses INTEGER NOT NULL DEFAULT 0,
    seconds      REAL    NOT NULL DEFAULT 0
);
"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class HashCache:
    """Used by core.find_duplicates: get/put file hashes keyed by (path, kind) and validated by size + mtime."""

    def __init__(self, database):
        self.db, self.hits, self.misses, self._pending = database, 0, 0, 0

    def get(self, path, size, mtime, kind):
        with self.db.lock:
            try:
                row = self.db.conn.execute(
                    "SELECT hash FROM hash_cache WHERE path=? AND kind=? AND size=? AND mtime=?",
                    (str(path), kind, size, mtime)).fetchone()
            except sqlite3.Error:
                row = None
        if row:
            self.hits += 1
            return row[0]
        self.misses += 1
        return None

    def put(self, path, size, mtime, kind, digest):
        with self.db.lock:
            try:
                self.db.conn.execute(
                    "INSERT OR REPLACE INTO hash_cache(path, kind, size, mtime, hash, updated) VALUES (?,?,?,?,?,?)",
                    (str(path), kind, size, mtime, digest, time.time()))
                self._pending += 1
                if self._pending >= 200:
                    self.db.conn.commit()
                    self._pending = 0
            except sqlite3.Error:
                pass

    def flush(self):
        with self.db.lock:
            try:
                self.db.conn.commit()
            except sqlite3.Error:
                pass
        self._pending = 0

    def reset_counters(self):
        self.hits = self.misses = 0


class Database:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()        # scans run in worker threads, the UI in the main one
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error:
            pass
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.conn.commit()
        self.hash_cache = HashCache(self)
        self.maintenance()

    def close(self):
        with self.lock:
            try:
                self.conn.commit()
                self.conn.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ meta
    def get_meta(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))
            self.conn.commit()

    def maintenance(self):
        """Trim old log lines and stale cache rows (run once at startup)."""
        with self.lock:
            self.conn.execute("DELETE FROM activity_log WHERE id <= (SELECT COALESCE(MAX(id),0) FROM activity_log) - ?",
                              (LOG_KEEP,))
            self.conn.execute("DELETE FROM hash_cache WHERE updated < ?", (time.time() - CACHE_MAX_AGE_DAYS * 86400,))
            self.conn.commit()

    # ------------------------------------------------------------------ undo
    def push_undo(self, label, moves):
        """Store one undoable batch. moves = [(src, dst), ...]."""
        with self.lock:
            cur = self.conn.execute("INSERT INTO undo_batches(label, created_at) VALUES (?, ?)", (label, _now()))
            bid = cur.lastrowid
            self.conn.executemany("INSERT INTO undo_moves(batch_id, position, src, dst) VALUES (?,?,?,?)",
                                  [(bid, i, str(s), str(d)) for i, (s, d) in enumerate(moves)])
            self.conn.commit()
        return bid

    def pop_undo(self):
        """Mark the newest active batch as undone (history is kept)."""
        with self.lock:
            self.conn.execute("""UPDATE undo_batches SET status='undone'
                                 WHERE id = (SELECT MAX(id) FROM undo_batches WHERE status='active')""")
            self.conn.commit()

    def load_undo_stack(self, limit=UNDO_LIMIT):
        """-> [(label, [(Path src, Path dst), ...])] oldest first. Moves whose file vanished are dropped;
        a batch with no surviving moves is marked 'stale' so memory and database stay in step."""
        with self.lock:
            batches = self.conn.execute(
                "SELECT id, label FROM undo_batches WHERE status='active' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            out = []
            for bid, label in reversed(batches):
                rows = self.conn.execute("SELECT src, dst FROM undo_moves WHERE batch_id=? ORDER BY position",
                                         (bid,)).fetchall()
                moves = [(Path(s), Path(d)) for s, d in rows if Path(d).exists()]
                if moves:
                    out.append((label, moves))
                else:
                    self.conn.execute("UPDATE undo_batches SET status='stale' WHERE id=?", (bid,))
            self.conn.commit()
        return out

    def undo_history(self, limit=50):
        """Newest first: [(id, label, created_at, status, move_count)]."""
        with self.lock:
            return self.conn.execute("""SELECT b.id, b.label, b.created_at, b.status, COUNT(m.id)
                                        FROM undo_batches b LEFT JOIN undo_moves m ON m.batch_id = b.id
                                        GROUP BY b.id ORDER BY b.id DESC LIMIT ?""", (limit,)).fetchall()

    # ------------------------------------------------------------------- log
    def add_log(self, message, ts=None):
        with self.lock:
            self.conn.execute("INSERT INTO activity_log(ts, message) VALUES (?, ?)", (ts or _now(), message))
            self.conn.commit()

    def recent_logs(self, limit=500, query=""):
        """Newest first, formatted like the old text log: '[2026-01-01 12:00:00] message'."""
        sql, args = "SELECT ts, message FROM activity_log", []
        if query:
            sql += " WHERE message LIKE ? ESCAPE '\\'"
            args.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [f"[{ts}] {msg}" for ts, msg in rows]

    def clear_logs(self):
        with self.lock:
            self.conn.execute("DELETE FROM activity_log")
            self.conn.commit()

    # ----------------------------------------------------------------- rules
    def get_rules(self):
        with self.lock:
            return self.conn.execute("SELECT pattern, folder FROM rules ORDER BY position").fetchall()

    def set_rules(self, rules):
        with self.lock:
            self.conn.execute("DELETE FROM rules")
            self.conn.executemany("INSERT INTO rules(position, pattern, folder) VALUES (?,?,?)",
                                  [(i, p, f) for i, (p, f) in enumerate(rules)])
            self.conn.commit()

    # --------------------------------------------------------- scan history
    def add_scan(self, kind, root, scanned=0, groups=0, dup_files=0, wasted_bytes=0,
                 cache_hits=0, cache_misses=0, seconds=0.0):
        with self.lock:
            self.conn.execute("""INSERT INTO scan_history(ts, kind, root, scanned, groups, dup_files, wasted_bytes,
                                 cache_hits, cache_misses, seconds) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (_now(), kind, str(root), scanned, groups, dup_files, wasted_bytes,
                               cache_hits, cache_misses, seconds))
            self.conn.commit()

    def scan_history(self, limit=20):
        with self.lock:
            return self.conn.execute("""SELECT ts, kind, root, scanned, groups, dup_files, wasted_bytes,
                                        cache_hits, cache_misses, seconds
                                        FROM scan_history ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()

    def stats(self):
        """Totals for the Activity Log page."""
        with self.lock:
            q = lambda sql: self.conn.execute(sql).fetchone()[0] or 0       # noqa: E731
            return {
                "actions": q("SELECT COUNT(*) FROM undo_batches"),
                "files_moved": q("SELECT COUNT(*) FROM undo_moves m JOIN undo_batches b ON b.id=m.batch_id"),
                "undone": q("SELECT COUNT(*) FROM undo_batches WHERE status='undone'"),
                "scans": q("SELECT COUNT(*) FROM scan_history"),
                "wasted_bytes": q("SELECT MAX(wasted_bytes) FROM scan_history"),
                "cached_hashes": q("SELECT COUNT(*) FROM hash_cache"),
                "log_lines": q("SELECT COUNT(*) FROM activity_log"),
            }

    # ------------------------------------------------------------- migration
    def migrate_legacy(self, journal_file=None, log_file=None, rules=None):
        """One-time import of the pre-SQLite files (kept on disk untouched). rules = [(pattern, folder)] from QSettings."""
        if self.get_meta("migrated_journal") is None:
            try:
                data = json.loads(Path(journal_file).read_text(encoding="utf-8")) if journal_file else []
            except (OSError, ValueError, TypeError):
                data = []
            for e in data[-UNDO_LIMIT:] if isinstance(data, list) else []:
                try:
                    self.push_undo(str(e.get("label", "Earlier action")), [(s, d) for s, d in e.get("moves", [])])
                except (TypeError, ValueError, AttributeError):
                    continue
            self.set_meta("migrated_journal", "1")
        if self.get_meta("migrated_log") is None:
            try:
                lines = Path(log_file).read_text(encoding="utf-8", errors="replace").splitlines() if log_file else []
            except OSError:
                lines = []
            with self.lock:
                for line in lines[-500:]:
                    ts, msg = _now(), line
                    if line.startswith("[") and "] " in line:
                        head, msg = line[1:].split("] ", 1)
                        ts = head
                    self.conn.execute("INSERT INTO activity_log(ts, message) VALUES (?, ?)", (ts, msg))
                self.conn.commit()
            self.set_meta("migrated_log", "1")
        if self.get_meta("migrated_rules") is None:
            if rules and not self.get_rules():
                self.set_rules(rules)
            self.set_meta("migrated_rules", "1")
