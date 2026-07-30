"""SQLite persistence for the CBSE video studio web app."""
import sqlite3, os, json, time

DB_PATH = os.environ.get("CBSE_DB", os.path.join(os.path.dirname(__file__), "studio.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS pdfs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    class       TEXT NOT NULL,
    subject     TEXT,
    title       TEXT,
    path        TEXT UNIQUE NOT NULL,
    pages       INTEGER,
    words       INTEGER,
    chapter_number INTEGER,
    chapter_name TEXT,
    identified_method TEXT DEFAULT 'none',
    text_preview TEXT DEFAULT '',
    created_at  REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id       INTEGER REFERENCES pdfs(id),
    slug         TEXT UNIQUE NOT NULL,
    title        TEXT,
    subject      TEXT,
    class        TEXT,
    template_id  TEXT DEFAULT 'science',
    status       TEXT DEFAULT 'draft',        -- draft|rendering|ready|needs_rework
    created_at   REAL DEFAULT (strftime('%s','now')),
    updated_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER REFERENCES videos(id),
    version_no   INTEGER,
    script_json  TEXT,                          -- full script.json snapshot
    note         TEXT,
    author       TEXT DEFAULT 'system',
    created_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER REFERENCES videos(id),
    version_id   INTEGER REFERENCES versions(id),
    kind         TEXT DEFAULT 'note',           -- note|rework|approve|reject
    text         TEXT,
    author       TEXT DEFAULT 'user',
    status       TEXT DEFAULT 'open',           -- open|resolved
    created_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER REFERENCES videos(id),
    version_id   INTEGER REFERENCES versions(id),
    seg_index    INTEGER,
    path         TEXT,
    prompt       TEXT,
    status       TEXT DEFAULT 'pending',        -- pending|approved|rejected
    feedback     TEXT,
    created_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS templates (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    subject      TEXT,
    description  TEXT,
    config_json  TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_pdf ON videos(pdf_id);
CREATE INDEX IF NOT EXISTS idx_versions_video ON versions(video_id);
CREATE INDEX IF NOT EXISTS idx_feedback_video ON feedback(video_id);
CREATE INDEX IF NOT EXISTS idx_images_video ON images(video_id);

CREATE TABLE IF NOT EXISTS render_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    version_no      INTEGER NOT NULL DEFAULT 1,
    status          TEXT DEFAULT 'pending_script_approval',
                    -- pending_script_approval|script_rejected|script_approved
                    -- queued|claimed|rendering|preview_ready|preview_rejected
                    -- finalizing|ready|failed
    agent           TEXT,                              -- which agent claimed this
    script_duration_cap REAL DEFAULT 60.0,             -- max seconds per script
    preview_url     TEXT,
    final_url       TEXT,
    thumbnail_url   TEXT,
    metadata        TEXT DEFAULT '{}',                 -- JSON: title,tags,description
    note            TEXT,
    enqueued_at     REAL DEFAULT (strftime('%s','now')),
    claimed_at      REAL,
    preview_ready_at REAL,
    completed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_queue_video ON render_queue(video_id);
CREATE INDEX IF NOT EXISTS idx_queue_status ON render_queue(status);

CREATE TABLE IF NOT EXISTS pdf_diagrams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id       INTEGER NOT NULL REFERENCES pdfs(id) ON DELETE CASCADE,
    page_number  INTEGER NOT NULL,
    image_path   TEXT,
    full_page_path TEXT,
    description  TEXT,
    bbox         TEXT,
    width        INTEGER,
    height       INTEGER,
    methods      TEXT DEFAULT '{}',
    status       TEXT DEFAULT 'pending',
    metadata     TEXT DEFAULT '{}',
    created_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_diagrams_pdf ON pdf_diagrams(pdf_id);
"""

SCRIPTS_TABLE = """
CREATE TABLE IF NOT EXISTS scripts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    slug         TEXT,
    title        TEXT,
    segment_idx  INTEGER NOT NULL DEFAULT 0,
    segment_text TEXT,
    image_prompt TEXT,
    visual_ref   TEXT,
    audio_ref    TEXT,
    duration_ms  INTEGER,
    version_no   INTEGER DEFAULT 1,
    metadata     TEXT DEFAULT '{}',
    created_at   REAL DEFAULT (strftime('%s','now')),
    updated_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_scripts_video ON scripts(video_id);
CREATE INDEX IF NOT EXISTS idx_scripts_slug ON scripts(slug);
"""

SETTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS watch_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    recursive INTEGER DEFAULT 1,
    enabled INTEGER DEFAULT 1,
    last_scan_at REAL,
    created_at REAL DEFAULT (strftime('%s','now'))
);
"""

CURRICULUM_VIEW = """
CREATE VIEW IF NOT EXISTS curriculum AS
WITH book_codes (class, subject, code, chapters) AS (
    VALUES
    ('9',  'Science',                      'iesc', 13),
    ('9',  'Mathematics',                  'iemh', 8),
    ('9',  'Social Science',               'iest', 9),
    ('9',  'English',                      'iebe', 8),
    ('9',  'Health & Physical Education',  'iehp', 6),
    ('9',  'Sanskrit',                     'ihsh', 16),
    ('9',  'Hindi',                        'ihga', 12),
    ('9',  'Urdu',                         'iuju', 12),
    ('9',  'Vocational/Language',          'iekv', 12),
    ('9',  'Language',                     'iemr', 12),
    ('10', 'Science',                      'jesc', 13),
    ('10', 'Social Science',               'jess', 8),
    ('10', 'Mathematics',                  'jemh', 8),
    ('10', 'Health & Physical Education',  'jehp', 6),
    ('10', 'Hindi (Kritika)',              'jhkr', 5),
    ('10', 'Hindi (Kshitij)',              'jhks', 17),
    ('10', 'Hindi (Vyakaran)',             'jhva', 10),
    ('10', 'Sanskrit',                     'jsab', 10),
    ('10', 'Urdu (Gulzar-e-Urdu)',         'juge', 10),
    ('10', 'Urdu (Jaan Pehchan)',          'jujp', 10),
    ('10', 'Urdu (Nai Awaz)',             'june', 10),
    ('10', 'Sanskrit (Shemushi)',          'jusc', 10),
    ('10', 'Sanskrit (Vyakaran)',          'jusr', 10),
    ('11', 'Chemistry',                    'kech', 14),
    ('11', 'Physics',                      'keph', 15),
    ('11', 'Mathematics',                  'kemh', 16),
    ('11', 'Geography',                    'kegy', 10),
    ('11', 'History/Heritage Crafts',      'kehs', 11),
    ('11', 'Health & Physical Education',  'kehp', 6),
    ('12', 'Chemistry',                    'lech', 16),
    ('12', 'Physics',                      'leph', 15),
    ('12', 'Mathematics',                  'lemh', 13),
    ('12', 'Geography',                    'legy', 12),
    ('12', 'History/Heritage Crafts',      'lehs', 14)
)
SELECT class, subject, code, chapters FROM book_codes;
"""

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS render_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    version_no      INTEGER NOT NULL DEFAULT 1,
    status          TEXT DEFAULT 'pending_script_approval',
    agent           TEXT,
    script_duration_cap REAL DEFAULT 60.0,
    preview_url     TEXT,
    final_url       TEXT,
    thumbnail_url   TEXT,
    metadata        TEXT DEFAULT '{}',
    note            TEXT,
    enqueued_at     REAL DEFAULT (strftime('%s','now')),
    claimed_at      REAL,
    preview_ready_at REAL,
    completed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_queue_video ON render_queue(video_id);
CREATE INDEX IF NOT EXISTS idx_queue_status ON render_queue(status);
"""

DIAGRAMS_TABLE = """
CREATE TABLE IF NOT EXISTS pdf_diagrams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id       INTEGER NOT NULL REFERENCES pdfs(id) ON DELETE CASCADE,
    page_number  INTEGER NOT NULL,
    image_path   TEXT,
    full_page_path TEXT,
    description  TEXT,
    bbox         TEXT,
    width        INTEGER,
    height       INTEGER,
    methods      TEXT DEFAULT '{}',
    status       TEXT DEFAULT 'pending',
    metadata     TEXT DEFAULT '{}',
    created_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_diagrams_pdf ON pdf_diagrams(pdf_id);
"""

COMPREHENSIVE_TABLE = """
CREATE TABLE IF NOT EXISTS pdf_comprehensive (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_id       INTEGER NOT NULL REFERENCES pdfs(id) ON DELETE CASCADE,
    page_number  INTEGER NOT NULL,
    result_json  TEXT NOT NULL DEFAULT '{}',
    updated_at   REAL DEFAULT (strftime('%s','now')),
    UNIQUE(pdf_id, page_number)
);
CREATE INDEX IF NOT EXISTS idx_comprehensive_pdf ON pdf_comprehensive(pdf_id);
"""

def init():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.executescript(SCRIPTS_TABLE)
    conn.executescript(QUEUE_TABLE)
    conn.executescript(DIAGRAMS_TABLE)
    conn.executescript(SETTINGS_TABLE)
    conn.executescript(COMPREHENSIVE_TABLE)
    try:
        conn.execute("SELECT 1 FROM curriculum LIMIT 1")
    except Exception:
        conn.executescript(CURRICULUM_VIEW)
    conn.commit()
    # seed templates from json files
    tdir = os.path.join(os.path.dirname(__file__), "..", "..", "pipeline", "templates")
    for fn in os.listdir(tdir) if os.path.isdir(tdir) else []:
        if fn.endswith(".json"):
            with open(os.path.join(tdir, fn)) as f:
                t = json.load(f)
            conn.execute(
                "INSERT OR REPLACE INTO templates(id,name,subject,description,config_json) VALUES(?,?,?,?,?)",
                (t["id"], t["name"], t.get("subject"), t.get("description"), json.dumps(t)),
            )
    conn.commit()
    conn.close()

def q(conn, sql, params=()):
    return conn.execute(sql, params)

def qone(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()

def commit(conn):
    conn.commit()
