#!/usr/bin/env bash
# Download and extract the most recent Project Quarm database dump from
# SecretsOTheP/EQMacEmu (utils/sql/database_full).
#
# Usage: scripts/fetch_latest_db.sh [--check] [--force]
#   --check  Only report the latest upstream dump and whether it is present locally.
#   --force  Re-download and re-extract even if the latest dump is already present.
#
# Env:
#   QUARM_DATA_DIR  Where to store dumps (default: <repo>/data)
#   GITHUB_TOKEN    Optional; raises the GitHub API rate limit.
set -euo pipefail

REPO="SecretsOTheP/EQMacEmu"
DUMP_PATH="utils/sql/database_full"
API_URL="https://api.github.com/repos/${REPO}/contents/${DUMP_PATH}?ref=main"
NAME_RE='^quarm_[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}_[0-9]{2}\.tar\.gz$'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${QUARM_DATA_DIR:-$(dirname "$SCRIPT_DIR")/data}"
CURRENT_DIR="$DATA_DIR/current"

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

for cmd in curl jq tar git; do
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
tarball="$DATA_DIR/$name"

local_version="$(cat "$CURRENT_DIR/VERSION" 2>/dev/null || true)"
echo "Latest upstream dump: $name"
echo "Local version:        ${local_version:-<none>}"

if (( check_only )); then
  [[ "$local_version" == "$version" ]] && exit 0 || exit 1
fi

if [[ "$local_version" == "$version" && -f "$tarball" ]] && (( ! force )); then
  echo "Already up to date."
  exit 0
fi

mkdir -p "$DATA_DIR"
tmp="$(mktemp "$DATA_DIR/.download.XXXXXX")"
tmp_extract=""
trap 'rm -rf "$tmp" ${tmp_extract:+"$tmp_extract"}' EXIT

if [[ -f "$tarball" && "$(git hash-object "$tarball")" == "$sha" ]] && (( ! force )); then
  echo "Tarball already downloaded and verified."
else
  echo "Downloading $url"
  curl -sSfL --retry 3 -o "$tmp" "$url"
  actual_sha="$(git hash-object "$tmp")"
  if [[ "$actual_sha" != "$sha" ]]; then
    echo "Checksum mismatch: expected $sha, got $actual_sha" >&2
    exit 1
  fi
  chmod 644 "$tmp"
  mv "$tmp" "$tarball"
fi

# Extract into a fresh directory, then swap it in.
tmp_extract="$(mktemp -d "$DATA_DIR/.extract.XXXXXX")"
chmod 755 "$tmp_extract"
tar -xzf "$tarball" -C "$tmp_extract"

# Upstream member names embed the timestamp with colons (e.g. quarm_2026-09-27-22:44.sql).
# Provide stable, colon-free symlinks for tooling.
(
  cd "$tmp_extract"
  for f in *.sql; do
    stable="$(sed -E 's/_[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}:[0-9]{2}\.sql$/.sql/' <<<"$f")"
    [[ "$stable" != "$f" ]] && ln -s "$f" "$stable"
  done
)
echo "$version" > "$tmp_extract/VERSION"

rm -rf "$CURRENT_DIR"
mv "$tmp_extract" "$CURRENT_DIR"
tmp_extract=""

# Only the most recent dump is kept.
find "$DATA_DIR" -maxdepth 1 -name 'quarm_*.tar.gz' ! -name "$name" -delete

echo "Extracted to $CURRENT_DIR:"
ls -l "$CURRENT_DIR"
