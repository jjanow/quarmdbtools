"""Fetch and convert the latest database dump (runs scripts/fetch_latest_db.sh)."""
import subprocess

from quarmtools import common

NAME = "fetch"
SUMMARY = "Download the latest Project Quarm dump and convert it to data/quarm.sqlite."


def add_arguments(ap):
    ap.add_argument("--check", action="store_true",
                    help="only report upstream vs local version; exit 1 if out of date")
    ap.add_argument("--force", action="store_true", help="re-download and re-convert")


def run(args):
    cmd = ["bash", str(common.SCRIPTS_DIR / "fetch_latest_db.sh")]
    cmd += [flag for flag, on in (("--check", args.check), ("--force", args.force)) if on]
    return subprocess.call(cmd)
