# quarmdbtools

Local tools for analyzing the [Project Quarm](https://www.projectquarm.com/) EverQuest server database.

Project Quarm publishes full database dumps in the
[SecretsOTheP/EQMacEmu](https://github.com/SecretsOTheP/EQMacEmu/tree/main/utils/sql/database_full)
repository. This repo converts the **most recent** dump into a local SQLite database
(`data/quarm.sqlite`). Older dumps and the raw MariaDB SQL are not kept.

## Requirements

- `bash`, `curl`, `jq`, `tar`, `git`
- Python 3.8+ (standard library only)

## Getting the database

```sh
scripts/fetch_latest_db.sh          # fetch + convert the latest dump (no-op if already current)
scripts/fetch_latest_db.sh --check  # report upstream vs local version; exit 1 if out of date
scripts/fetch_latest_db.sh --force  # re-download and re-convert

sqlite3 data/quarm.sqlite "select id, Name from items where Name = 'cloth cap'"
```

The script:

1. Lists `utils/sql/database_full` through the GitHub contents API and picks the newest
   `quarm_YYYY-MM-DD-HH_MM.tar.gz` by name.
2. Compares it with the version recorded in `data/quarm.sqlite` (`_meta.source_version`) and
   stops if they match.
3. Downloads the tarball to a temp dir under `data/` and checks it against the git blob SHA the API reports.
4. Extracts it and runs `scripts/mariadb_to_sqlite.py` to build `data/quarm.sqlite`.
5. Deletes the temp dir (tarball and extracted SQL). This happens even if a step fails, and a failed
   run leaves the existing database untouched.

Set `GITHUB_TOKEN` to raise the API rate limit, or `QUARM_DATA_DIR` to keep the database outside `data/`.
`data/` is gitignored.

## SQLite conversion

`scripts/mariadb_to_sqlite.py` is a stdlib-only Python script. `fetch_latest_db.sh` runs it, or you
can run it on an extracted dump yourself with `scripts/mariadb_to_sqlite.py --src DIR`. It reads the
dump line by line, parses each value (including MariaDB backslash escapes), and inserts it with bound
parameters. The latest dump takes about 15 seconds and produces about 85 MB. It writes to a temp file
and only replaces `data/quarm.sqlite` on success. Anything it doesn't recognize (a new column type,
table option or value literal) stops the conversion with the file and line number rather than being
skipped.

How MariaDB features map to SQLite:

- **Types:** columns use SQLite affinities (INTEGER/REAL/NUMERIC/TEXT/BLOB). The original MariaDB
  types (e.g. `mediumint(8) unsigned`) are kept in the `_column_types` table.
- **Case sensitivity:** text columns from case-insensitive (`*_ci`) collations get `COLLATE NOCASE`,
  so string comparisons ignore case as they do in MariaDB.
- **Keys:** primary keys are preserved. Secondary and unique keys become indexes named
  `<table>__<key>`. The one `FULLTEXT` key (`qs_player_speech.to`, player schema) becomes a plain index.
- **Zero dates:** values like `'0000-00-00 00:00:00'` are kept verbatim as text.
- **Dropped:** `ON UPDATE CURRENT_TIMESTAMP`, `AUTO_INCREMENT` counters, engine, charset and
  row-format options.
- **Provenance:** the `_meta` table records the source dump version and the conversion time.

## Notes on the dump

Each upstream tarball contains these files. The first four are loaded into the SQLite database.

| File | Contents |
|---|---|
| `quarm_<ts>.sql` | Game content: items, NPCs, spawns, loot, spells, zones, ... |
| `player_tables_<ts>.sql` | Character/account schema (no rows) |
| `login_tables_<ts>.sql` | Login server schema (no rows) |
| `data_tables_<ts>.sql` | Server config: `rule_sets`/`rule_values`, `variables`, `command_settings`, ... |
| `drop_system.sql`, `readme.txt` | MariaDB upgrade helper and import notes (not used) |

- It's a MariaDB dump (`mariadb-dump`) and won't load in the MySQL 8 client, so SQLite is the
  supported way to query it here.
- The main dump contains about 68 content tables. Most are MyISAM with latin1 collation; the dump file itself is UTF-8.
- Upstream data quirk: a few item names end in a literal NUL byte (e.g. item 3860,
  `Glyphed Skin Sample - Wrist, Right\0`). The converter keeps it, so exact `=` matches on those names miss.

See [TODO.md](TODO.md) for planned work.
