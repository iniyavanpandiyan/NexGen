"""Settings management for CBSE Video Studio — persisted in SQLite settings table."""
import os, json, time, threading, logging
from pathlib import Path

log = logging.getLogger("cbse-studio.settings")

DEFAULT_WATCH_INTERVAL = 60  # seconds between scans


def get_setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r:
        try:
            return json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            return r["value"]
    return default


def set_setting(conn, key, value):
    if not isinstance(value, str):
        value = json.dumps(value)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, strftime('%s','now'))",
        (key, value),
    )


def get_watch_folders(conn):
    rows = conn.execute(
        "SELECT * FROM watch_folders ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def add_watch_folder(conn, path, recursive=True):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise ValueError(f"Not a valid directory: {path}")
    conn.execute(
        "INSERT OR IGNORE INTO watch_folders (path, recursive, enabled) VALUES (?, ?, 1)",
        (path, 1 if recursive else 0),
    )
    conn.commit()
    return {"path": path, "recursive": recursive}


def remove_watch_folder(conn, folder_id):
    conn.execute("DELETE FROM watch_folders WHERE id=?", (folder_id,))
    conn.commit()


def scan_watch_folder(path, recursive=True, extensions=(".pdf",)):
    found = []
    base = Path(path)
    if not base.exists():
        return found
    if recursive:
        iterator = base.rglob("*")
    else:
        iterator = base.glob("*")
    for f in iterator:
        if f.is_file() and f.suffix.lower() in extensions:
            found.append(str(f.resolve()))
    return found


def run_watch_scan(settings_py_module, db_module):
    """Background thread: scans all enabled watch folders for new PDFs."""
    from db import get_conn

    conn = get_conn()
    folders = get_watch_folders(conn)
    conn.close()

    for folder in folders:
        if not folder["enabled"]:
            continue
        try:
            found = scan_watch_folder(folder["path"], bool(folder["recursive"]))
            conn2 = get_conn()
            existing = {
                r["path"]
                for r in conn2.execute("SELECT path FROM pdfs").fetchall()
            }
            new_count = 0
            for path in found:
                if path not in existing:
                    from catalog import scan

                    new_count += 1
            if new_count:
                log.info(
                    f"Watch folder scan: {new_count} new PDFs in {folder['path']}"
                )
            conn2.close()
        except Exception as e:
            log.warning(f"Watch folder scan error for {folder['path']}: {e}")


def start_watch_background(catalog_module):
    """Start a background thread that periodically scans watch folders."""

    def _watcher():
        from db import get_conn
        import time as _time

        while True:
            _time.sleep(DEFAULT_WATCH_INTERVAL)
            try:
                from catalog import scan
                conn = get_conn()
                folders = get_watch_folders(conn)
                conn.close()
                for folder in folders:
                    if not folder["enabled"]:
                        continue
                    try:
                        found = scan_watch_folder(
                            folder["path"], bool(folder["recursive"])
                        )
                        conn2 = get_conn()
                        existing = {
                            r["path"]
                            for r in conn2.execute(
                                "SELECT path FROM pdfs"
                            ).fetchall()
                        }
                        conn2.close()
                        for path in found:
                            if path not in existing:
                                catalog_module.scan(force=False)
                                break
                    except Exception as e:
                        log.warning(
                            f"Watch folder scan error for {folder['path']}: {e}"
                        )
            except Exception:
                pass

    t = threading.Thread(target=_watcher, daemon=True)
    t.start()
    log.info("Watch folder background scanner started")
    return t
