#!/usr/bin/env bash
# Download the most recent Project Quarm database dump from SecretsOTheP/EQMacEmu
# (utils/sql/database_full), convert it to SQLite, and remove the MariaDB dump.
#
# Usage: scripts/fetch_latest_db.sh [--check] [--force]
#   --check  Only report the latest upstream dump and whether the local database is built from it.
#   --force  Re-download and re-convert even if the local database is already current.
#
# Env:
#   QUARM_DATA_DIR  Where to store the database (default: <repo>/data)
#   GITHUB_TOKEN    Optional; raises the GitHub API rate limit.
set -euo pipefail

REPO="SecretsOTheP/EQMacEmu"
DUMP_PATH="utils/sql/database_full"
API_URL="https://api.github.com/repos/${REPO}/contents/${DUMP_PATH}?ref=main"
NAME_RE='^quarm_[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}_[0-9]{2}\.tar\.gz$'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${QUARM_DATA_DIR:-$(dirname "$SCRIPT_DIR")/data}"
DB_FILE="$DATA_DIR/quarm.sqlite"

check_only=0
force=0
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    --force) force=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

for cmd in curl jq tar git python3; do
  command -v "$cmd" >/dev/null || { echo "Missing required command: $cmd" >&2; exit 1; }
done

auth=()
[[ -n "${GITHUB_TOKEN:-}" ]] && auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}")

listing="$(curl -sSfL -H "Accept: application/vnd.github+json" "${auth[@]}" "$API_URL")"
latest="$(jq -c --arg re "$NAME_RE" \
  '[.[] | select(.type == "file" and (.name | test($re)))] | sort_by(.name) | last // empty' \
  <<<"$listing")"
[[ -n "$latest" ]] || { echo "No dumps matching $NAME_RE found upstream" >&2; exit 1; }

name="$(jq -r .name <<<"$latest")"
sha="$(jq -r .sha <<<"$latest")"          # git blob SHA of the file
url="$(jq -r .download_url <<<"$latest")"
version="${name#quarm_}"; version="${version%.tar.gz}"

# The version the local SQLite database was built from (recorded by mariadb_to_sqlite.py).
local_version="$(python3 -I -c '
import sqlite3, sys
try:
    db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    print(db.execute("select value from _meta where key = ?", ("source_version",)).fetchone()[0])
except Exception:
    pass
' "$DB_FILE")"
echo "Latest upstream dump: $name"
echo "Local version:        ${local_version:-<none>}"

if (( check_only )); then
  [[ "$local_version" == "$version" ]] && exit 0 || exit 1
fi

if [[ "$local_version" == "$version" ]] && (( ! force )); then
  echo "Already up to date."
  exit 0
fi

# Everything MariaDB-specific lives in this work dir and is removed on exit, success or not.
mkdir -p "$DATA_DIR"
work="$(mktemp -d "$DATA_DIR/.work.XXXXXX")"
trap 'rm -rf "$work"' EXIT

echo "Downloading $url"
curl -sSfL --retry 3 -o "$work/$name" "$url"
actual_sha="$(git hash-object "$work/$name")"
if [[ "$actual_sha" != "$sha" ]]; then
  echo "Checksum mismatch: expected $sha, got $actual_sha" >&2
  exit 1
fi

mkdir "$work/sql"
tar -xzf "$work/$name" -C "$work/sql"

# Upstream member names embed the timestamp with colons (e.g. quarm_2026-09-27-22:44.sql);
# the converter expects the colon-free names (quarm.sql, player_tables.sql, ...).
(
  cd "$work/sql"
  for f in *.sql; do
    stable="$(sed -E 's/_[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}:[0-9]{2}\.sql$/.sql/' <<<"$f")"
    [[ "$stable" != "$f" ]] && ln -s "$f" "$stable"
  done
)
echo "$version" > "$work/sql/VERSION"

"$SCRIPT_DIR/mariadb_to_sqlite.py" --src "$work/sql" --out "$DB_FILE"

echo "Removed MariaDB dump; database is at $DB_FILE"
