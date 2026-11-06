import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS identity (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT NOT NULL, acronym TEXT, motto TEXT, origin TEXT,
  constitution_sha TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personality_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version INTEGER NOT NULL UNIQUE,
  traits TEXT NOT NULL, justification TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visitors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT NOT NULL UNIQUE,
  display_name TEXT, created_at TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL CHECK (category IN ('fact','event','learning','visitor','reflection','external')),
  certainty TEXT NOT NULL DEFAULT 'memory'
    CHECK (certainty IN ('verified','hypothesis','interpretation','memory','unknown')),
  content TEXT NOT NULL, source TEXT,
  visitor_id INTEGER REFERENCES visitors(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  uses INTEGER NOT NULL DEFAULT 0,
  importance REAL NOT NULL DEFAULT 0.5, last_used_at TEXT, archived_at TEXT,
  pinned INTEGER NOT NULL DEFAULT 0, emb TEXT, emb_name TEXT
);
CREATE TABLE IF NOT EXISTS memory_links (
  a INTEGER NOT NULL, b INTEGER NOT NULL, weight REAL NOT NULL, PRIMARY KEY (a, b)
);
CREATE TABLE IF NOT EXISTS visitor_profiles (
  visitor_id INTEGER PRIMARY KEY REFERENCES visitors(id) ON DELETE CASCADE,
  summary TEXT, open_threads TEXT, msgs_since INTEGER NOT NULL DEFAULT 0, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS memory_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL, old_content TEXT NOT NULL, new_content TEXT NOT NULL,
  reason TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  visitor_id INTEGER NOT NULL REFERENCES visitors(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  detail TEXT, level TEXT NOT NULL DEFAULT 'info',
  prev_hash TEXT, hash TEXT
);
CREATE TABLE IF NOT EXISTS emotion_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, event TEXT NOT NULL, label TEXT,
  valence REAL, arousal REAL
);
CREATE TABLE IF NOT EXISTS emotional_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, state_text TEXT NOT NULL, own_label TEXT,
  cause TEXT, uncertainty TEXT, valence REAL, arousal REAL
);
CREATE TABLE IF NOT EXISTS emotional_lexicon (
  id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL UNIQUE, description TEXT,
  uses INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autobiography (
  id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, why TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','achieved','dropped')),
  progress TEXT, source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('personality','goal')),
  payload TEXT NOT NULL, rationale TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  decision_note TEXT, created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS thoughts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  visitor_id INTEGER REFERENCES visitors(id) ON DELETE CASCADE,
  content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','done')),
  priority INTEGER NOT NULL DEFAULT 5, source TEXT NOT NULL DEFAULT 'seed',
  times_investigated INTEGER NOT NULL DEFAULT 0, last_investigated_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS investigations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  self_prompt TEXT NOT NULL, conclusion TEXT, uncertainties TEXT,
  tokens INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
  day TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_mem_cat ON memories(category, deleted_at);
CREATE INDEX IF NOT EXISTS idx_msg_visitor ON messages(visitor_id, id);
"""


class Database:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def init(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(audit_log)")}
            if "hash" not in cols:  # migração de bancos da Fase 1
                c.execute("ALTER TABLE audit_log ADD COLUMN prev_hash TEXT")
                c.execute("ALTER TABLE audit_log ADD COLUMN hash TEXT")
            mcols = {r[1] for r in c.execute("PRAGMA table_info(memories)")}
            if "uses" not in mcols:
                c.execute("ALTER TABLE memories ADD COLUMN uses INTEGER NOT NULL DEFAULT 0")
            for col, ddl in (("importance", "REAL NOT NULL DEFAULT 0.5"), ("last_used_at", "TEXT"), ("archived_at", "TEXT"),
                             ("pinned", "INTEGER NOT NULL DEFAULT 0"), ("emb", "TEXT"), ("emb_name", "TEXT")):
                if col not in mcols:
                    c.execute(f"ALTER TABLE memories ADD COLUMN {col} {ddl}")
                    if col == "pinned":  # memórias antigas do admin e as verificadas não esmaecem
                        c.execute("UPDATE memories SET pinned = 1 WHERE certainty = 'verified' OR source IS NULL")

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
