# TODO

## Loading the data
- [ ] Decide on a query backend:
  - MariaDB in Docker (`docker compose` + import script). This is the most faithful option and needs no conversion.
  - Convert to SQLite for a zero-server, single-file DB. This needs a converter that handles the MariaDB-isms
    (`/*M!...*/` comments, `ENGINE=MyISAM`, `unsigned`, backtick quoting, multi-row `INSERT`s).
- [ ] Script to (re)import `data/current/` into the chosen backend after `fetch_latest_db.sh`.
- [ ] Optionally chain fetch + import into one "refresh" command.
- [ ] Decide whether server config (`data_tables.sql`: `rule_values`, `variables`, ...) is worth exposing in analysis, e.g. rules that affect drop and XP rates.

## Schema exploration
- [ ] Document the key tables and how they join:
  - Items: `items`
  - NPCs: `npc_types`, `npc_types_metadata`, `npc_faction`, `npc_spells*`
  - Spawns: `spawn2` → `spawngroup` → `spawnentry` → `npc_types`; `spawn_conditions`, `spawn_events`, `grid*`
  - Loot: `npc_types.loottable_id` → `loottable` → `loottable_entries` → `lootdrop` → `lootdrop_entries` → `items`; `global_loot`
  - Merchants: `merchantlist`
  - Spells: `spells_new` / `spells_en`
  - Factions: `faction_list`, `faction_list_mod`, `npc_faction_entries`
  - Zones: `zone`, `zone_points`, `doors`, `object`, `ground_spawns`, `forage`, `fishing`
  - Tradeskills: `tradeskill_recipe`, `tradeskill_recipe_entries`
  - Era gating: `content_flags` (and `content_flags`/`min_expansion`-style columns on content tables)
- [ ] Generate a schema reference (table/column list) automatically from the dump.

## Analysis tools (ideas)
- [ ] Item lookup: stats, which NPCs drop it (with drop chance), where those NPCs spawn, and merchants that sell it.
- [ ] NPC lookup: spawn locations, spawn chance in each spawngroup, respawn timers, loot table, and faction hits.
- [ ] Zone summaries: NPC list, notable drops, and level ranges.
- [ ] Effective drop-rate math across loottable multipliers, lootdrop probabilities, and spawn chances.
- [ ] Spell lookup and which NPCs cast a spell.
- [ ] Output formats: CLI tables, plus CSV/JSON export.
- [ ] Pick an implementation language and set up the project skeleton (deps, lint, tests).
