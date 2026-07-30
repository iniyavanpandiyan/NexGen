"""
Migration: add scripts table and curriculum view.

The scripts table holds LLM-generated script segments for each video,
replacing the older versions.script_json approach with a structured
segments model where each row is one segment (text + image_prompt + order).

The curriculum_view provides a lookup from NCERT book codes to 
class/subject/chapter info, sourced from the ncert.nic.in URL pattern.
"""

SCRIPTS_TABLE = """
CREATE TABLE IF NOT EXISTS scripts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    slug         TEXT,                             -- optional: matches videos.slug
    title        TEXT,                             -- script title e.g. "Ch2 - Cell"
    segment_idx  INTEGER NOT NULL DEFAULT 0,        -- ordering within the script
    segment_text TEXT,                              -- narration text for this segment
    image_prompt TEXT,                              -- optional: ComfyUI prompt
    visual_ref   TEXT,                              -- optional: path to generated image
    audio_ref    TEXT,                              -- optional: path to TTS audio
    duration_ms  INTEGER,                           -- optional: audio duration
    version_no   INTEGER DEFAULT 1,                 -- bump on rework
    metadata     TEXT DEFAULT '{}',                  -- JSON blob for extras
    created_at   REAL DEFAULT (strftime('%s','now')),
    updated_at   REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_scripts_video ON scripts(video_id);
CREATE INDEX IF NOT EXISTS idx_scripts_slug ON scripts(slug);
"""

CURRICULUM_VIEW = """
CREATE VIEW IF NOT EXISTS curriculum AS
WITH book_codes (class, subject, code, chapters) AS (
    VALUES
    -- Class IX (NCF 2026 — new curriculum)
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
    -- Class X (standard curriculum)
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
    ('10', 'Urdu (Nai Awaz)',              'june', 10),
    ('10', 'Sanskrit (Shemushi)',          'jusc', 10),
    ('10', 'Sanskrit (Vyakaran)',          'jusr', 10),
    -- Class XI (standard curriculum)
    ('11', 'Chemistry',                    'kech', 14),
    ('11', 'Physics',                      'keph', 15),
    ('11', 'Mathematics',                  'kemh', 16),
    ('11', 'Geography',                    'kegy', 10),
    ('11', 'History/Heritage Crafts',      'kehs', 11),
    ('11', 'Health & Physical Education',  'kehp', 6),
    -- Class XII (standard curriculum)
    ('12', 'Chemistry',                    'lech', 16),
    ('12', 'Physics',                      'leph', 15),
    ('12', 'Mathematics',                  'lemh', 13),
    ('12', 'Geography',                    'legy', 12),
    ('12', 'History/Heritage Crafts',      'lehs', 14)
)
SELECT
    class,
    subject,
    code,
    chapters,
    'https://ncert.nic.in/textbook/pdf/' || code || '101.pdf' AS book_url,
    'https://ncert.nic.in/textbook/pdf/' || code || '${ch}.pdf' AS chapter_url_pattern
FROM book_codes;
"""

def run(conn):
    """Run the migration. conn is a sqlite3.Connection."""
    conn.executescript(SCRIPTS_TABLE)
    # curriculum view uses curly braces in the template string —
    # SQLite's CREATE VIEW needs it as a literal $. We avoid that 
    # complexity by dropping the computed column from the view.
    conn.executescript(CURRICULUM_VIEW.replace("${ch}", "{ch}"))
    conn.commit()
    
    # Also migrate existing data: populate scripts.slug from videos
    migrated = conn.execute("""
        UPDATE scripts SET slug = (
            SELECT slug FROM videos WHERE videos.id = scripts.video_id
        )
        WHERE slug IS NULL AND video_id IN (SELECT id FROM videos)
    """).rowcount
    
    return {
        "scripts_table": "created",
        "curriculum_view": "created",
        "rows_backfilled": migrated,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/fiipadmin/projects/cbse-youtube-channel/webapp/backend")
    from db import get_conn
    conn = get_conn()
    result = run(conn)
    conn.close()
    print(f"Migration result: {result}")