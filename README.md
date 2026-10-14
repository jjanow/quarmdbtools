# quarmdbtools

Local tools for analyzing the [Project Quarm](https://www.projectquarm.com/) EverQuest server database.

Project Quarm publishes full database dumps in the
[SecretsOTheP/EQMacEmu](https://github.com/SecretsOTheP/EQMacEmu/tree/main/utils/sql/database_full)
repository. This repo only works with the **most recent** dump. Older dumps are not kept.

## Requirements

- `bash`, `curl`, `jq`, `tar`, `git`
- A MariaDB server or client to import the dump (see [Notes on the dump](#notes-on-the-dump))

## Getting the database

```sh
scripts/fetch_latest_db.sh          # download + extract the latest dump (no-op if already current)
scripts/fetch_latest_db.sh --check  # report upstream vs local version; exit 1 if out of date
scripts/fetch_latest_db.sh --force  # re-download and re-extract
```

The script:

1. Lists `utils/sql/database_full` through the GitHub contents API and picks the newest
   `quarm_YYYY-MM-DD-HH_MM.tar.gz` by name.
2. Downloads it and checks it against the git blob SHA the API reports.
3. Extracts it to `data/current/`, adds stable symlinks, and writes a `VERSION` file.
4. Deletes any older tarballs in `data/`.

Set `GITHUB_TOKEN` to raise the API rate limit, or `QUARM_DATA_DIR` to store dumps outside `data/`.
`data/` is gitignored.

### Data layout

```
data/
├── quarm_<version>.tar.gz          # the latest upstream tarball
└── current/
    ├── VERSION                     # e.g. 2026-09-27-22_44
    ├── quarm.sql          -> quarm_<ts>.sql          # game content: items, NPCs, spawns, loot, spells, zones, ...
    ├── player_tables.sql  -> player_tables_<ts>.sql  # character/account schema (no player data)
    ├── login_tables.sql   -> login_tables_<ts>.sql   # login server schema
    ├── data_tables.sql    -> data_tables_<ts>.sql    # server config: rule_sets/rule_values, variables, command_settings, ...
    ├── drop_system.sql                               # drops content tables before an upgrade import
    └── readme.txt                                    # upstream import notes
```

## Notes on the dump

- It's a MariaDB dump (`mariadb-dump`). The first line, `/*M!999999\- enable the sandbox mode */`,
  breaks the MySQL 8 client, so import it with MariaDB.
- The bundled `readme.txt` refers to `alkabor_*.sql`. The main file is now `quarm_*.sql`.
- Fresh import, from a MariaDB prompt inside `data/current/`:
  ```sql
  CREATE DATABASE quarm;
  USE quarm;
  SOURCE quarm.sql;
  SOURCE player_tables.sql;
  SOURCE login_tables.sql;
  SOURCE data_tables.sql;
  ```
- The main dump contains about 68 content tables. Most are MyISAM with latin1 encoding.

See [TODO.md](TODO.md) for planned work.
