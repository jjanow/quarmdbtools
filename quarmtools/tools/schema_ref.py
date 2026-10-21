"""Regenerate docs/schema_reference.md (runs scripts/gen_schema_ref.py)."""
import subprocess
import sys

from quarmtools import common

NAME = "schema-ref"
SUMMARY = "Regenerate docs/schema_reference.md from the database (after a fetch)."


def add_arguments(ap):
    ap.add_argument("--db", default=str(common.DEFAULT_DB), help="SQLite database to read (default: %(default)s)")
    ap.add_argument("--out", help="Markdown file to write (default: docs/schema_reference.md)")


def run(args):
    cmd = [sys.executable, str(common.SCRIPTS_DIR / "gen_schema_ref.py")]
    cmd += ["--db", args.db]
    if args.out:
        cmd += ["--out", args.out]
    return subprocess.call(cmd)
