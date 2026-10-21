"""Paths and database helpers shared by the tools."""
import os
import sqlite3
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
# Same default and override as scripts/fetch_latest_db.sh.
DATA_DIR = Path(os.environ.get("QUARM_DATA_DIR") or REPO_ROOT / "data")
DEFAULT_DB = DATA_DIR / "quarm.sqlite"


def open_readonly(path):
    """Open an SQLite file read-only. Raises FileNotFoundError if it doesn't exist."""
    if not path.is_file():
        raise FileNotFoundError(f"database not found: {path}")
    uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def db_info(path=DEFAULT_DB):
    """{"path", "exists", "source_version", "converted_at"} for the status line on the home page."""
    info = {"path": str(path), "exists": path.is_file(), "source_version": None, "converted_at": None}
    if info["exists"]:
        try:
            conn = open_readonly(path)
            try:
                meta = dict(conn.execute("SELECT key, value FROM _meta").fetchall())
            finally:
                conn.close()
            info["source_version"] = meta.get("source_version")
            info["converted_at"] = meta.get("converted_at")
        except sqlite3.Error:
            pass
    return info
