# TODO

## Loading the data
- [x] Query backend: SQLite, via `scripts/mariadb_to_sqlite.py` (MariaDB-in-Docker remains an option if exact MariaDB semantics are ever needed).
- [x] Script to build the database from an extracted dump.
- [x] `fetch_latest_db.sh` fetches, converts, and removes the MariaDB dump in one step.
- [x] FTS5 name search: `items_fts`, `npc_types_fts`, `spells_new_fts`, built by the converter (`--fts-only` rebuilds them).
- [x] Add tests for the converter's value parser (`tests/test_value_parser.py`).
- [x] Add tests for schema conversion (column options, keys, table options) and a small end-to-end dump (`tests/test_schema_conversion.py`).
- [ ] Decide whether server config (`data_tables.sql`: `rule_values`, `variables`, ...) is worth exposing in analysis, e.g. rules that affect drop and XP rates. Recommendation in `docs/server_config.md`; awaiting a decision.

## Schema exploration
- [x] Document the key tables and how they join: `docs/schema.md`.
- [x] Generate a schema reference (table/column list) automatically from the dump: `scripts/gen_schema_ref.py` → `docs/schema_reference.md`.

## Analysis tools (ideas)
- [x] Research needs: words/pages each character still needs for spell research (`./quarm research`).
- [ ] Item lookup: stats, which NPCs drop it (with drop chance), where those NPCs spawn, and merchants that sell it.
- [ ] NPC lookup: spawn locations, spawn chance in each spawngroup, respawn timers, loot table, and faction hits.
- [ ] Zone summaries: NPC list, notable drops, and level ranges.
- [ ] Effective drop-rate math across loottable multipliers, lootdrop probabilities, and spawn chances.
- [ ] Spell lookup and which NPCs cast a spell.
- [ ] Output formats: CLI tables, plus CSV/JSON export.
- [x] Pick an implementation language and set up the project skeleton: stdlib Python, `./quarm` entry point with tool modules in `quarmtools/tools/` and a local web front end. (Lint not set up.)
