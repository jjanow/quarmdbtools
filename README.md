# quarmdbtools

Local tools for analyzing the [Project Quarm](https://www.projectquarm.com/) EverQuest server database.

Project Quarm publishes full database dumps in the
[SecretsOTheP/EQMacEmu](https://github.com/SecretsOTheP/EQMacEmu/tree/main/utils/sql/database_full)
repository. This repo converts the **most recent** dump into a local SQLite database
(`data/quarm.sqlite`). Older dumps and the raw MariaDB SQL are not kept.

## Requirements

- `bash`, `curl`, `jq`, `tar`, `git`
- Python 3.8+ (standard library only), with an SQLite that includes FTS5 (standard in python.org and
  most distro builds)

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

After fetching a new dump, run `scripts/gen_schema_ref.py` to refresh `docs/schema_reference.md`.

Set `GITHUB_TOKEN` to raise the API rate limit, or `QUARM_DATA_DIR` to keep the database outside `data/`.
`data/` is gitignored.

## SQLite conversion

`scripts/mariadb_to_sqlite.py` is a stdlib-only Python script. `fetch_latest_db.sh` runs it, or you
can run it on an extracted dump yourself with `scripts/mariadb_to_sqlite.py --src DIR`
(`--fts-only FILE` only rebuilds the name-search tables in an existing database). It reads the
dump line by line, parses each value (including MariaDB backslash escapes), and inserts it with bound
parameters. The latest dump takes about 15 seconds and produces about 91 MB. It writes to a temp file
and only replaces `data/quarm.sqlite` on success. Anything it doesn't recognize (a new column type,
table option or value literal) stops the conversion with the file and line number rather than being
skipped.

How MariaDB features map to SQLite:

- **Types:** columns use SQLite affinities (INTEGER/REAL/NUMERIC/TEXT/BLOB). The original MariaDB
  types (e.g. `mediumint(8) unsigned`, including `zerofill`) are kept in the `_column_types` table.
- **Case sensitivity:** text columns from case-insensitive (`*_ci`) collations get `COLLATE NOCASE`,
  so string comparisons ignore case as they do in MariaDB.
- **Keys:** primary keys are preserved. Secondary and unique keys become indexes named
  `<table>__<key>`. The one `FULLTEXT` key (`qs_player_speech.to`, player schema) becomes a plain index.
- **Zero dates:** values like `'0000-00-00 00:00:00'` are kept verbatim as text.
- **Dropped:** `ON UPDATE CURRENT_TIMESTAMP`, `AUTO_INCREMENT` counters, engine, charset and
  row-format options.
- **Provenance:** the `_meta` table records the source dump version and the conversion time.

### Name search (FTS5)

`items_fts`, `npc_types_fts` and `spells_new_fts` index `items.Name`, `npc_types.name` and
`spells_new.name`. Each has columns `name` and `id` (rowid = id); join back on `id`. Punctuation and
`_` split words, so `pup gnoll` finds `a_gnoll_pup`; `flam*` is a prefix search, and `ORDER BY rank`
sorts the best matches first. Item names are indexed without their trailing NUL. The tables are a
snapshot built by the converter (about 4 MB). They don't follow edits to the base tables; rebuild them
with `scripts/mariadb_to_sqlite.py --fts-only data/quarm.sqlite`.

```sh
sqlite3 data/quarm.sqlite "select i.id, i.Name from items_fts f join items i on i.id = f.id
                           where items_fts match 'cloth cap' order by rank"
```

Wrap user-supplied terms in double quotes (doubling any `"`) so characters such as `'`, `-` or `:`
aren't parsed as FTS5 syntax: `match '"kilva''s" flam*'`.

## Research needs

`scripts/research_needs.py` lists the spell research words and pages a character still needs. It
writes `data/research_needs.html`, a self-contained page you open in a browser, and
`data/research_needs.csv`.

```sh
scripts/research_needs.py                      # reads the Zeal exports in ~/Games/EQQuarm/drive_c/Quarm
scripts/research_needs.py --eq-dir /path/to/EQ # or set QUARM_EQ_DIR
scripts/research_needs.py --era 3              # merchants as before Planes of Power
```

First, write fresh exports in game with Zeal: `/outputfile spellbook` and `/outputfile inventory` on
each character. The page has:

- **A character picker.** It lists every character with a `<Name>-Spellbook.txt` export, plus a
  "New <class>" entry for each research class (Enchanter, Magician, Necromancer, Wizard) for
  characters you haven't made yet.
- **A Words & pages tab.** Each row is one item, with needed / owned / still need, the spells that
  use it, and where your copies are. Each item links to its PQDI page.
- **A Spells tab.** Each row is one missing spell, with its components, its status and any
  alternative recipe. The status is one of:
  - missing words;
  - ready;
  - ready (words shared): every word is owned, but other missing spells need the same copies;
  - buy: a merchant sells the finished scroll, so there's no need to research it;
  - scroll owned.

  The tab also shows a shopping route: the fewest zones that sell every buyable spell, picked
  greedily.
- **A Duplicate column.** It flags:
  - words used by several spells;
  - spare copies;
  - pages whose two halves are different items with the same name. For example, "Part of Tasarin's
    Grimoire Pg. 390" is items 16080 and 16081, and a recipe needs one of each. These get `#id`
    after the name.
- **Merchants.** Spells and components show who sells them, with zone, merchant and base price.
  Words for buyable spells are left out of the counts unless you tick "Count words for spells I
  can buy". A merchant counts only if it spawns, and only if its merchant list entry, spawn and
  zone are live in `--era`, which defaults to 4 (Planes of Power, so Plane of Knowledge vendors
  count). The dump's own `World:CurrentExpansion` rule still says 3.0 (Luclin). This is PQ
  Companion's spell-checklist rule for "buyable", plus the era check, which PQ Companion doesn't do.
- **Sorting, filters and export.** Click any column header to sort. You can filter by search text,
  type, "above my level" and "still to get", and download the current view as CSV.

Owned counts combine every character's bags, bank and the shared bank. Each export repeats the shared
bank, so only the newest one's copy counts. A character's class comes from their spellbook, and their
level comes from PQ Companion's `user.db` when it's readable. Recipes come from `data/quarm.sqlite`.
A recipe's class is set by its research tome, since some spells have different recipes for different
classes.

## Schema docs

- [docs/schema.md](docs/schema.md): the key tables (items, NPCs, spawns, loot, merchants, spells,
  factions, zones, tradeskills, era gating), how they join, match rates and caveats, and an example
  query for each area.
- [docs/schema_reference.md](docs/schema_reference.md): every table with its row count, columns
  (original MariaDB type, affinity, NOT NULL, default, NOCASE, primary key) and indexes. It is
  generated, so don't edit it by hand. Regenerate it after fetching a new dump with
  `scripts/gen_schema_ref.py`. The output is deterministic, so `git diff` shows what changed between
  dumps.
- [docs/server_config.md](docs/server_config.md): which server rules (`rule_values`, `variables`)
  affect drop rates, XP and respawns, and a recommendation on exposing them in analysis.

## Tests

```sh
python3 -m unittest discover tests
```

Tests use only the standard library. They cover:

- the converter's value parser (`tests/test_value_parser.py`): number and NULL literals, string escapes,
  row and statement boundaries, and rejecting literals it doesn't support;
- schema conversion (`tests/test_schema_conversion.py`): column types and options, collations, keys,
  table options, `_column_types`, errors, and an end-to-end conversion of a small dump;
- the FTS5 name-search tables (`tests/test_fts.py`);
- research needs (`tests/test_research_needs.py`): recipe classes and dedupe, shared-bank merging,
  alternative recipes, owned scrolls and duplicate flags.

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
  `items_fts` indexes the names without the NUL.

See [TODO.md](TODO.md) for planned work.
