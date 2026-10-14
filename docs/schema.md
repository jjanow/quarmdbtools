# Key tables and how they join

This is a hand-written guide to the content tables in `data/quarm.sqlite`, the SQLite conversion of the
Project Quarm (EQMacEmu) dump. It was checked against dump `2026-09-27-22_44` (`_meta.source_version`).
Every join below was tested against the data, and every SQL block was run with `sqlite3 -readonly`.
The full table and column list is in `docs/schema_reference.md`, which is generated separately.

Conventions:

- **Match rate** is the share of child rows whose key exists in the parent. "Orphans" are the child rows
  that don't match.
- **(inferred)** marks behaviour that comes from general EQEmu/EQMacEmu knowledge or from patterns in the
  data, and that could not be confirmed against the server code. The server source is not part of this repo.
- Unless a query says otherwise, it ignores era gating and returns all eras. See [Era gating](#era-gating)
  for the predicate that restricts results to the live era.

## Join diagram

```
                                 ┌──────────── zone ─────────────┐
                                 │ short_name (text key)         │ zoneidnumber (int key; zone.id is NOT a key)
            spawn2.zone, doors.zone, zone_points.zone,           grid.zoneid, grid_entries.zoneid, object.zoneid,
            spawn_conditions.zone, spawn_events.zone,            ground_spawns.zoneid, forage.zoneid, fishing.zoneid,
            spawn_condition_values.zone, doors.dest_zone         zone_points.target_zone_id, global_loot.zone (text)

 spawn2 ──spawngroupID──▶ spawngroup ◀──spawngroupID── spawnentry ──npcID──▶ npc_types
   │ (pathgrid, zone→zoneidnumber) ──▶ grid ◀──(gridid, zoneid)── grid_entries
   │ (_condition, zone) ──▶ spawn_conditions ◀──(cond_id, zone)── spawn_events
   │                                         ◀──(id, zone)─────── spawn_condition_values

 npc_types ──loottable_id──▶ loottable ◀──loottable_id── loottable_entries ──lootdrop_id──▶ lootdrop
                                 ▲                                                          ▲
                   global_loot.loottable_id                                     lootdrop_entries.lootdrop_id
                                                                                lootdrop_entries.item_id ──▶ items
 npc_types ──merchant_id──▶ merchantlist.merchantid ; merchantlist.item ──▶ items
 npc_types ──npc_spells_id──▶ npc_spells (parent_list ──▶ npc_spells) ◀── npc_spells_entries ──spellid──▶ spells_new
 npc_types ──npc_faction_id──▶ npc_faction (primaryfaction ──▶ faction_list)
                               ◀── npc_faction_entries ──faction_id──▶ faction_list ◀── faction_list_mod
 npc_types ──armortint_id──▶ npc_types_tint ; npc_types.emoteid ──▶ npc_emotes.emoteid
 items.{clickeffect,proceffect,worneffect,focuseffect,scrolleffect} ──▶ spells_new.id
 tradeskill_recipe ◀──recipe_id── tradeskill_recipe_entries ──item_id──▶ items (only when iscontainer=0)
 object.itemid, ground_spawns.item, forage.Itemid, fishing.Itemid, doors.keyitem ──▶ items

 Era gating, on most content tables: min_expansion / max_expansion (REAL), content_flags / content_flags_disabled
 (a flag name from content_flags.flag_name). The current era comes from rule_values 'World:CurrentExpansion'.
```

## Corrections to the TODO list

Every table named in the TODO exists. Some of them, and some of the assumed join keys, are not what the
list implies:

| TODO entry | Reality in this dump |
|---|---|
| `npc_types_metadata` | Exists but has **0 rows**. Use `npc_types.raid_target`, `npc_types.rare_spawn` and `npc_types.isquest` instead. |
| `npc_spells*` | `npc_spells` and `npc_spells_entries` are used. `npc_spells_effects` and `npc_spells_effects_entries` (30 rows each) are **not referenced by any NPC**, because `npc_types.npc_spells_effects_id` is 0 everywhere. |
| `spawn_conditions`, `spawn_events` | You also need `spawn_condition_values` (current values), which the list omits. |
| `spells_new` / `spells_en` | They are not two names for one table. `spells_en` is a **subset**: it lacks 35 discipline ids (4498–4678), 40 columns, and thousands of shared-column values differ. Use `spells_new` (inferred to be the table the server loads). |
| `merchantlist` | `merchantlist_temp` (runtime player-sold items) is empty. |
| `zone` | The keys are `short_name` (text) and `zoneidnumber` (int). `zone.id` is a surrogate key that never equals `zoneidnumber`, and nothing joins on it. About 20 zone names used by spawns, doors and zone points (instanced or custom zones) have **no `zone` row**. |
| `content_flags` | It's a 6-row flag table. Content tables have **two** text columns, `content_flags` and `content_flags_disabled`, plus REAL `min_expansion`/`max_expansion`. `zone` also has an `expansion` column, which is the zone's home expansion. |
| Loot chain | It's correct as listed. `loottable_entries` carries no era columns; all the other loot tables do. |
| Not listed but useful | `npc_emotes`, `npc_types_tint`, `level_exp_mods` (XP per level), `rule_values` (`World:CurrentExpansion`), `zone.zone_exp_multiplier`, `npc_types.exp_pct`. `object_contents` and `respawn_times` are empty runtime tables. |

## Items

`items` has 26,972 rows and 159 columns. The primary key is `id`. Note that the name column is `Name`, which
is case-insensitive (`COLLATE NOCASE`).

- Effect columns point at `spells_new.id`. 0 means none, and `proceffect = 65535` is a sentinel.
  Match rates for non-zero values: `clickeffect` 99.7% (5 dangling spell ids), `proceffect` 98.9%,
  `focuseffect` 99.4%, `worneffect` and `scrolleffect` 100%.
- Era gating: `min_expansion`/`max_expansion` are set on 3,874 items. Only 11 items have a non-NULL flag
  column, and it is always `''`.
  The rule `Expansion:UseItemExpansionSetting=true` strips era-inappropriate item properties (focus, skill
  mods, bane, rec/req level) rather than hiding the item (inferred, based on the rule's note).
- Watch out for names that end in a NUL byte (see README). Prefer `LIKE 'name%'`, the item id, or the
  `items_fts` name-search table (which indexes names without the NUL) over `=`.

```sql
-- Item stats plus the spells behind its click/proc/worn/focus effects.
SELECT i.id, i.Name, i.ac, i.hp, i.mana, i.slots, i.reqlevel, i.nodrop, i.min_expansion, i.max_expansion,
       ck.name AS click_spell, pr.name AS proc_spell, wn.name AS worn_spell, fc.name AS focus_spell
FROM items i
LEFT JOIN spells_new ck ON ck.id = i.clickeffect AND i.clickeffect > 0
LEFT JOIN spells_new pr ON pr.id = i.proceffect  AND i.proceffect  > 0
LEFT JOIN spells_new wn ON wn.id = i.worneffect  AND i.worneffect  > 0
LEFT JOIN spells_new fc ON fc.id = i.focuseffect AND i.focuseffect > 0
WHERE i.Name LIKE 'Cloak of Flames%' OR i.Name LIKE 'Short Sword of the Ykesha%';
```

## NPCs

`npc_types` has 18,033 rows. The primary key is `id`. Its foreign keys use **0 to mean "none"**:

| Column | Target | Non-zero rows | Match |
|---|---|---|---|
| `loottable_id` | `loottable.id` | 12,324 | 99.23% (95 rows / 62 ids dangling) |
| `merchant_id` | `merchantlist.merchantid` | 1,940 | 97.8%. 29 of the 43 misses are sentinels `1` and `999999`; 14 rows use ids with no list. |
| `npc_spells_id` | `npc_spells.id` | 5,405 | 99.98% (id 743 is missing) |
| `npc_faction_id` | `npc_faction.id` | 15,920 | 99.99% (ids 20002 and 19471 are missing) |
| `npc_spells_effects_id` | `npc_spells_effects.id` | 0 | unused |
| `armortint_id` | `npc_types_tint.id` | 254 | 100% |
| `emoteid` | `npc_emotes.emoteid` | 7,042 | 100% |

- `npc_types_metadata` is empty. The flags you'd want are on `npc_types` itself: `raid_target` (201 NPCs),
  `rare_spawn`, `isquest` and `exp_pct` (an XP percentage, 100 by default).
- **NPC id and zone.** Ids follow the convention `npc_id / 1000 = zone.zoneidnumber % 1000`. This holds for
  15,888 of 15,951 distinct (npc, zone) spawn pairs (99.6%). Plain `npc_id/1000 = zoneidnumber` only reaches
  97.4%, because alternate and instanced zones numbered 10xx (for example `towerbone` 1078 and `hate_instanced`
  1076) reuse their base zone's NPC ids. Exceptions:
  - Ids below 1000 are generic or shared NPCs (zone controllers, `Animation*`). There are 33 such pairs.
  - Custom content uses high prefixes with no `zone` row: the `6100xxx`–`6150xxx` series is the Tower of
    Frozen Shadow / "injustice" instance content, and `1501xxx` is shared by `cshome2`, `iceclad2` and `myriah`.
  - 927 NPC ids spawn in more than one zone, mostly instanced copies of the base zone. Treat the prefix as a
    convention and never as a join key. Always go through `spawnentry` → `spawn2`.
- There are **duplicate NPC rows** for the same named mob. Lord Nagafen has ids 32040, 32340 and 77500. Two of them
  (32340 and 77500) have `exp_pct=1`, and 77500 also uses a special loot table (`Special_VoxNaggy`). Group by id, not by name.
- 1,844 NPC types appear in no `spawnentry`. They're spawned by quests or scripts, or unused.
- **Name matching.** Names use `_` for spaces, and `_` is also a `LIKE` wildcard, so `LIKE 'Lord_Nagafen'`
  matches by accident. For word searches use `npc_types_fts` (see README), where `MATCH 'nagafen lord'`
  finds it in any word order.

```sql
-- NPC overview: faction, spell list (with parent), loot table, merchant list, emote set.
SELECT n.id, n.name, n.level, n.class, n.raid_target, n.exp_pct,
       nf.name AS npc_faction, fl.name AS primary_faction,
       ns.name AS spell_list, ns.parent_list,
       lt.name AS loottable, n.merchant_id, n.emoteid
FROM npc_types n
LEFT JOIN npc_faction  nf ON nf.id = n.npc_faction_id
LEFT JOIN faction_list fl ON fl.id = nf.primaryfaction
LEFT JOIN npc_spells   ns ON ns.id = n.npc_spells_id
LEFT JOIN loottable    lt ON lt.id = n.loottable_id
WHERE n.name LIKE 'Lord_Nagafen' OR n.name LIKE 'Lady_Vox';
```

## Spawns

The chain is `spawn2` (a spawn point in a zone) → `spawngroup` → `spawnentry` (a weighted NPC candidate) →
`npc_types`.

| Join | Rows | Match | Orphans |
|---|---|---|---|
| `spawn2.spawngroupID` → `spawngroup.id` | 48,739 | 99.71% | 140 rows, 16 ids (including 0) |
| `spawnentry.spawngroupID` → `spawngroup.id` | 26,332 | 99.98% | 6 rows |
| `spawnentry.npcID` → `npc_types.id` | 26,332 | 99.73% | 70 rows, 58 ids |
| `spawn2.zone` → `zone.short_name` | 48,739 | **89.84%** | 4,950 rows in 20 zone names with no `zone` row: `chardok_instanced`, `frostfragment`, `injustice`…`injustice50`, `dreadlands_instanced`, `bardingwood`, `charasis_instanced`, `iceclad2`, `mischiefhouse`, `myriah`, `cshome2`, `fireice`, `faydarkfragment`, and one NULL |
| `(spawn2.pathgrid, zone.zoneidnumber)` → `grid(id, zoneid)` | 10,496 with pathgrid > 0 | 98.34% | 174 rows |
| `grid_entries(gridid, zoneid)` → `grid(id, zoneid)` | 711,517 | 99.51% | 3,482 rows |
| `(spawn2._condition, spawn2.zone)` → `spawn_conditions(id, zone)` | 2,529 with _condition > 0 | 98.93% | 27 rows (`mischiefhouse`, `towerbone`, `burningwood`, `iceclad2`) |
| `spawn_events(cond_id, zone)` → `spawn_conditions(id, zone)` | 185 | 100% | none |
| `spawn_condition_values(id, zone)` → `spawn_conditions(id, zone)` | 212 | 98.58% | 3 |

Parent-side: 429 spawngroups are used by no `spawn2` row, and 41 spawngroups have no entries.

`grid.zoneid` holds `zoneidnumber`, but only 26% of grid rows match a `zone` row. The rest are grids left over
for zones that aren't in this server's `zone` table, such as zone ids 372, 409 and 700. Always join grids
through spawn2 together with the zone, because grid `id` is only unique **within** a zone.

Semantics:

- **`spawnentry.chance` is a relative weight within a spawngroup** (inferred). The weights sum to exactly 100
  in 14,112 of 14,323 groups, to less than 100 in 67 and to more than 100 in 144, so normalise:
  `chance / SUM(chance) per group`. The common values are 100, 25, 50, 20, 33, 10, 5 and 2.
- `spawngroup.spawn_limit` caps simultaneous spawns of the group (0 means unlimited, which covers 14,294 of
  14,358 groups). The `max_x`/`min_x`/... columns define a roaming box (588 groups). `despawn` and
  `despawn_timer` are for timed despawns (inferred).
- `spawn2.respawntime` and `variance` are **seconds** (inferred). Examples: 900 for Naxot Deepwater, and 64800
  (18 h) for the `fireice` raid copies. Classic raid targets such as Nagafen and Vox have `respawntime =
  1700000` (about 19.7 days). That looks like "effectively never", with repops driven by Quarm's quake system
  (`Quarm:EnableQuakes=true`, quake interval 7–10 days; see `docs/server_config.md`) (inferred).
- `spawn2.enabled = 0` on 4,023 rows. These are disabled points; filter them out unless a condition or event
  turns them on.
- `spawnentry.mintime`/`maxtime` restrict an entry to an EQ time-of-day window. Only 263 rows set them
  (inferred).
- **Conditions**: `spawn2._condition` (0 means none) names a condition of the same zone. The point is
  active when the condition's value equals `spawn2.cond_value`. `spawn_conditions.value` is the default,
  `spawn_condition_values` holds overrides, and `spawn_events` changes the value on an EQ-time schedule, for
  example day and night mobs in `commons` (inferred from the column names and EQEmu behaviour).
- `grid.type` and `type2` are the wander and pause modes. `grid_entries` holds the ordered waypoints.

```sql
-- Where does an NPC spawn, how likely is it to be the one chosen at each point, and how fast does it respawn?
SELECT n.name, s2.zone, s2.id AS spawn2_id, sg.name AS spawngroup,
       se.chance,
       ROUND(100.0 * se.chance / (SELECT SUM(chance) FROM spawnentry WHERE spawngroupID = se.spawngroupID), 1) AS pct_of_group,
       s2.respawntime, s2.variance, s2.enabled, s2._condition, s2.cond_value,
       sc.name AS condition_name, sc.value AS condition_default
FROM npc_types n
JOIN spawnentry se ON se.npcID = n.id
JOIN spawngroup sg ON sg.id = se.spawngroupID
JOIN spawn2 s2     ON s2.spawngroupID = sg.id
LEFT JOIN spawn_conditions sc ON sc.zone = s2.zone AND sc.id = s2._condition
WHERE n.name LIKE 'Lord_Nagafen' OR n.name LIKE 'Lady_Vox' OR n.name LIKE 'Naxot_Deepwater';
```

## Loot

The chain is `npc_types.loottable_id` → `loottable` → `loottable_entries` → `lootdrop` → `lootdrop_entries` →
`items`, plus `global_loot` → `loottable`.

| Join | Rows | Match | Orphans |
|---|---|---|---|
| `npc_types.loottable_id` (>0) → `loottable.id` | 12,324 | 99.23% | 95 rows |
| `loottable_entries.loottable_id` → `loottable.id` | 20,800 | 99.97% | 7 rows |
| `loottable_entries.lootdrop_id` → `lootdrop.id` | 20,800 | 99.83% | 36 rows, 21 ids |
| `lootdrop_entries.lootdrop_id` → `lootdrop.id` | 44,596 | 99.98% | 7 rows (including 0) |
| `lootdrop_entries.item_id` → `items.id` | 44,596 | 99.98% | 10 rows (item 0, and 7516–7521) |
| `global_loot.loottable_id` → `loottable.id` | 48 | 100% | none |

Parent-side: 301 loottables have no entries, 155 lootdrops have no entries, 81 lootdrops are used by no
loottable, and 16 loottables are used by neither NPCs nor global loot.

Columns and how loot is rolled. The algorithm is **inferred** from EQEmu's `loottables.cpp`. Don't treat an
effective-rate formula built on it as verified until it has been checked against EQMacEmu source.

- `loottable.mincash`/`maxcash`/`avgcoin` hold coin. `done` is an editor flag.
- `loottable_entries.multiplier` is how many times this lootdrop is rolled (1 in 20,514 rows; up to 6 or more).
  `probability` (0–100) is the chance each of those rolls happens at all (100 in 18,291 rows).
  `multiplier_min` is the number of rolls guaranteed regardless of `probability` (rarely set). `droplimit` and
  `mindrop` are the maximum and minimum items taken from the lootdrop per roll.
- `lootdrop_entries.chance` is a REAL percentage, from 0 to 200.
  - When `droplimit = 0` and `mindrop = 0` (11,153 entries), each item is rolled independently against
    `chance`, `multiplier` times.
  - Otherwise items are picked by weighted chance until `droplimit` is reached, with `mindrop` guaranteed.
  - Per-lootdrop chance totals are below 100 for 7,390 lootdrops, exactly 100 for 3,018 and above 100 for 562.
    Don't assume they are a probability distribution.
- `lootdrop_entries.equip_item` (whether the NPC wears the item), `item_charges`, `minlevel`/`maxlevel` (NPC
  level window; 0/127 and 0/255 are both "no restriction"), `min_looter_level`, `item_loot_lockout_timer`
  (set on 343 rows) and `disabled_chance` (non-zero on 5 rows) all exist.
- `global_loot` adds a loottable to every NPC that matches its filters. `zone` is a **text zoneidnumber** (for
  example `'48'`, or `'6100'` for instances with no `zone` row). `race`, `class` and `bodytype` are text,
  `min_level`/`max_level` use 0 to mean unbounded, `rare` and `raid` are `''`, and `enabled` is 0 for 34 of
  the 48 rows. `npc_types.skip_global_loot` is 0 everywhere.
- Server rules can scale all of this. See `docs/server_config.md` (`Zone:GlobalLootMultiplier`, the
  `Quarm:Enable*DoubleLoot` rules).

```sql
-- Which NPCs drop item 11621 (Cloak of Flames), with drop parameters, and where they spawn.
SELECT n.id AS npc_id, n.name AS npc, n.level,
       lte.multiplier, lte.probability AS table_prob, lte.droplimit, lte.mindrop,
       lde.chance AS item_chance, lde.multiplier AS item_mult,
       s2.zone, z.long_name, COUNT(DISTINCT s2.id) AS spawn_points
FROM lootdrop_entries lde
JOIN loottable_entries lte ON lte.lootdrop_id = lde.lootdrop_id
JOIN npc_types n           ON n.loottable_id  = lte.loottable_id
LEFT JOIN spawnentry se    ON se.npcID        = n.id
LEFT JOIN spawn2 s2        ON s2.spawngroupID = se.spawngroupID
LEFT JOIN zone z           ON z.short_name    = s2.zone      -- LEFT: ~10% of spawn2.zone values are not in zone
WHERE lde.item_id = 11621
GROUP BY n.id, lte.loottable_id, lte.lootdrop_id, s2.zone
ORDER BY n.name;
```

This query returns Lord Nagafen, Talendor and others. Rows with an empty zone are NPC types that never spawn
from `spawn2`.

## Merchants

`npc_types.merchant_id` → `merchantlist.merchantid`. `merchantlist` (`merchantid`, `slot`) holds 25,875 rows,
and `merchantlist.item` → `items.id` matches 99.85% (39 rows, 31 dangling item ids).

- 1,927 of the 1,940 NPCs with a merchant list are class 41 (merchant). 1,764 of the 1,940 have
  `merchant_id = npc id`.
- `merchant_id` values `1` and `999999` are placeholders. 151 merchant lists are used by no NPC.
- `probability` is 100 everywhere and `quantity` is 0 almost everywhere (limited stock in 23 rows).
  `faction_required` is -1100 in most rows, meaning no requirement (inferred).
- Era gating via `min_expansion`/`max_expansion` and the two flag columns.

```sql
-- Which spawned merchants sell item 13005 (Iron Ration), and in which zones.
SELECT n.id AS npc_id, n.name AS merchant, s2.zone, ml.slot, ml.min_expansion, ml.max_expansion
FROM merchantlist ml
JOIN npc_types n  ON n.merchant_id = ml.merchantid
JOIN spawnentry se ON se.npcID = n.id
JOIN spawn2 s2    ON s2.spawngroupID = se.spawngroupID
WHERE ml.item = 13005
GROUP BY n.id, s2.zone
ORDER BY s2.zone
LIMIT 10;
```

## Spells

- Use **`spells_new`**: 3,934 rows, ids 0–4678, 187 columns. `spells_en` (3,899 rows) has the same ids minus
  35 disciplines and 40 fewer columns. Names match on every shared id, but other values differ: `aoerange` on
  2,873 ids, `TargetAnim` on 1,828, `effect_limit_value1` on 914, and so on. Treat `spells_en` as a stale or
  alternate copy (inferred).
- NPC spell lists: `npc_types.npc_spells_id` → `npc_spells.id`. `npc_spells.parent_list` (0 means none;
  100% match) adds the parent list's entries. `npc_spells_entries.npc_spells_id` → `npc_spells.id` matches
  100%. `npc_spells_entries.spellid` → `spells_new.id` matches 99.67% (14 rows, 11 ids). `minlevel`/`maxlevel`
  on an entry restrict it by NPC level (inferred). Entries also carry era columns.
- `npc_spells.attack_proc`/`range_proc`/`defensive_proc` are spell ids. `attack_proc` matches only 89.5% (70
  of 668 non-zero rows point at missing spells 4570–6303).

```sql
-- Which NPCs can cast spell 'Lava Breath', counting spell lists inherited through npc_spells.parent_list.
SELECT n.id, n.name, n.level, ns.id AS npc_spells_id, ns.name AS list, nse.npc_spells_id AS list_with_spell,
       nse.minlevel, nse.maxlevel, nse.priority
FROM spells_new sp
JOIN npc_spells_entries nse ON nse.spellid = sp.id
JOIN npc_spells ns          ON nse.npc_spells_id IN (ns.id, ns.parent_list)
JOIN npc_types n            ON n.npc_spells_id = ns.id
WHERE sp.name = 'Lava Breath'
  AND n.level BETWEEN nse.minlevel AND nse.maxlevel
ORDER BY n.name;
```

## Factions

- `npc_types.npc_faction_id` → `npc_faction.id` (99.99%). `npc_faction.primaryfaction` → `faction_list.id`
  (100%) is the faction the NPC "is", the one shown when you /con it.
- `npc_faction_entries` (`npc_faction_id`, `faction_id`) → `npc_faction` and `faction_list`, both 100%.
  - `value` is the faction change applied when you kill the NPC.
  - `npc_value` is how members of that faction treat the NPC: 1 means assist, -1 means attack, 0 means
    neutral (inferred).
  - `temp` is 0 everywhere.
- `faction_list` holds `base`, `min_cap` and `max_cap`. `faction_list_mod.faction_id` → `faction_list.id`
  (100%) gives starting-faction modifiers keyed by `mod_name`: `r<race>` (5,771 rows), `c<class>` (840) and
  `d<deity>` (1,332).

```sql
-- Faction hits for killing an NPC (value) and how each faction regards the NPC (npc_value).
SELECT n.name, fl.id AS faction_id, fl.name AS faction, nfe.value AS hit_on_kill, nfe.npc_value, nfe.sort_order
FROM npc_types n
JOIN npc_faction_entries nfe ON nfe.npc_faction_id = n.npc_faction_id
JOIN faction_list fl         ON fl.id = nfe.faction_id
WHERE n.name LIKE 'Guard_Gehnus' OR n.id = 1031
ORDER BY n.name, nfe.sort_order;
```

## Zones

`zone` has 192 rows. Both `short_name` and `zoneidnumber` are unique. **`zone.id` is not `zoneidnumber`** and
is never used as a foreign key.

| Child | Key | Match | Notes |
|---|---|---|---|
| `spawn2.zone` | short_name | 89.84% | 20 instanced or custom zones have no `zone` row |
| `zone_points.zone` | short_name | 67.35% | 444 rows in 38 names without a `zone` row (`*_instanced`, `injustice*`, `bazaarxx`, ...) |
| `zone_points.target_zone_id` | zoneidnumber | 73.31% | Misses: 0 (16 rows), 61xx (instances, 28 rows each), 1026, 182, 186, ... |
| `doors.zone` | short_name | 74.82% | 2,066 rows in 51 names (arena or instance copies) |
| `doors.dest_zone` (excluding `'NONE'`/`''`) | short_name | 72.84% | `hateplaneb`, `sodecay`, `injustice*`, ... |
| `doors.keyitem` (>0) | items.id | 59.0% | All 68 misses are the value `1` (a placeholder) |
| `object.zoneid` | zoneidnumber | 100% | `object.itemid` → items 100% (only 18 non-zero) |
| `ground_spawns.zoneid` | zoneidnumber | 90.86% | 47 rows in 9 ids (186, 10xx); `item` 99.6% (0 and 27 dangle) |
| `forage.zoneid` | zoneidnumber | 98.31% | 21 rows (182, 230, 1020); `Itemid` 99.8% (0) |
| `fishing.zoneid` (>0) | zoneidnumber | 94.84% | zoneid 0 (2 rows) is presumably "any zone" (inferred); `Itemid` 100% |
| `grid.zoneid` | zoneidnumber | 26.25% | Leftover grids for zones not on this server |

- `zone.expansion` is the zone's home expansion: 0 classic (72 zones), 1 Kunark, 2 Velious, 3 Luclin,
  4 PoP, 99 (6 zones) and -1 (2 zones). Zone rows themselves are not gated: `min_expansion` and
  `max_expansion` are -1 on every row.
- `zone_exp_multiplier` is the per-zone XP modifier (ZEM). It is 1 in 87 zones, 0.1 in 4 and 0.75 in 2, and up to 1.7 elsewhere.
  `reducedspawntimers` is 1 in 94 zones (see the respawn-reduction rules in `docs/server_config.md`).
  `hotzone` is 1 in all 192 zones.
- `forage.chance` and `fishing.chance` are relative weights per zone (inferred). `ground_spawns` has a
  bounding box, `max_allowed` and `respawn_timer`.

```sql
-- Per-zone content counts. Note the two different zone keys: short_name (text) and zoneidnumber (int).
SELECT z.zoneidnumber, z.short_name, z.long_name, z.expansion, z.zone_exp_multiplier,
       (SELECT COUNT(*) FROM spawn2        WHERE zone   = z.short_name)   AS spawn_points,
       (SELECT COUNT(*) FROM zone_points   WHERE zone   = z.short_name)   AS zone_lines,
       (SELECT COUNT(*) FROM doors         WHERE zone   = z.short_name)   AS doors,
       (SELECT COUNT(*) FROM object        WHERE zoneid = z.zoneidnumber) AS objects,
       (SELECT COUNT(*) FROM ground_spawns WHERE zoneid = z.zoneidnumber) AS ground_spawns,
       (SELECT COUNT(*) FROM forage        WHERE zoneid = z.zoneidnumber) AS forage,
       (SELECT COUNT(*) FROM fishing       WHERE zoneid = z.zoneidnumber) AS fishing,
       (SELECT COUNT(*) FROM grid          WHERE zoneid = z.zoneidnumber) AS grids
FROM zone z
WHERE z.short_name IN ('qeynos', 'qeytoqrg', 'soldungb', 'kael');
```

## Tradeskills

- `tradeskill_recipe_entries.recipe_id` → `tradeskill_recipe.id` matches 100%. Each entry's role is given by
  its counts: `componentcount > 0` means it's consumed, `successcount > 0` means it's produced on success,
  `failcount > 0` means it's returned on failure, and `iscontainer = 1` means it's the container.
- `item_id` → `items.id` matches 91.07% overall, but that number is misleading:
  - For `iscontainer = 0` rows, 7 of 40,825 are orphans.
  - For `iscontainer = 1` rows, 4,836 of 13,406 "miss" because a small `item_id` (12, 15–19, 21, 32–34,
    39, 40, ...) is a **world-container bag type** that matches `items.bagtype`, not an item id. For
    example, 17 is the forge type (inferred).
- `tradeskill_recipe.tradeskill` is a skill id (55–75; 63 is smithing, inferred). It also has `trivial`,
  `nofail`, `quest`, `enabled` and era columns.

```sql
-- Recipes that produce an item, with their components and container.
-- iscontainer=1 rows: item_id is either a real item or, for world containers, a bag type number (not an items.id).
SELECT r.id, r.name, r.tradeskill, r.trivial, e.iscontainer, e.componentcount, e.successcount,
       e.item_id, i.Name AS item
FROM tradeskill_recipe r
JOIN tradeskill_recipe_entries e ON e.recipe_id = r.id
LEFT JOIN items i ON i.id = e.item_id
WHERE r.id IN (SELECT recipe_id FROM tradeskill_recipe_entries WHERE successcount > 0 AND item_id = 13004)
ORDER BY r.id, e.iscontainer DESC, e.componentcount DESC;
```

## Era gating

Gating uses two mechanisms, applied per row.

**1. Expansion window.** These tables have `min_expansion` and `max_expansion` (REAL): `items`, `spawn2`,
`spawnentry`, `loottable`, `lootdrop`, `lootdrop_entries`, `global_loot`, `merchantlist`,
`npc_spells_entries`, `tradeskill_recipe`, `doors`, `zone_points`, `object`, `ground_spawns`, `forage`,
`fishing` and `zone`. `loottable_entries` has none.

- `-1` means unbounded, and is the value on most rows (for example 44,799 of 48,739 `spawn2` rows).
  `99` is used as an open-ended max.
- Numbering, from `zone.expansion` and the `Character:DefaultExpansions` note: 0 Classic, 1 Kunark,
  2 Velious, 3 Luclin, 4 PoP. Higher values appear on a few rows: up to 9 on doors and up to 6 on items.
- **Fractional values exist**: 0.3, 0.6, 0.9, 1.3, 1.9 and 2.9. They look like patch milestones within an
  era (inferred). For example, Kithicor's spawns switch at 0.3, together with the `OldPlane_Hate_Sky` flag.
  A few rows use `-99` as a "never" marker: 2 spawnentry rows have min = max = -99.
- **Which "current expansion" applies is inferred.** `rule_values` has two candidates:
  - `World:CurrentExpansion = 3.0` (a REAL, which suits the fractional values). This means Luclin.
  - `Expansion:CurrentExpansion = -1`, whose note says "-1 = All Expansion". That looks like the mainline
    EQEmu rule left at its default.

  The REAL type and the fractional data suggest `World:CurrentExpansion` is the one that drives gating.
- **Whether the bounds are inclusive is unverified.** The data has hand-off pairs within one group, where an
  entry with `max_expansion = X` sits beside a replacement with `min_expansion = X`:
  - in lootdrops: 0.3 → 0.3 (35 pairs) and 0.6 → 0.6 (49 pairs)
  - in spawngroups: 2.0 → 3.0 (442 pairs) and 2.9 → 3.0 (474 pairs)

  The same-value hand-offs only avoid overlap if the window is half-open, `min <= cur < max`.
- **Evidence for the other reading:** 274 gated rows have `min_expansion = max_expansion` (not -1):
  - spawn2: 222, of which 218 are disabled `chardok` points
  - spawnentry: 43
  - lootdrop: 5, all at 1.0
  - lootdrop_entries: 4

  Under a half-open window these rows are dead in every era. Under inclusive bounds they are live only at
  exactly that value. Other pairs leave gaps under either reading. The examples use half-open (inferred).
- **For the default filter it barely matters.** At `World:CurrentExpansion = 3.0`, the two readings differ
  only on rows with `max_expansion = 3.0`. There are 3 such rows: 1 in spawn2, 1 in spawnentry and 1 in
  lootdrop.

**2. Content flags.** `content_flags` has 6 rows: `flag_name` and `enabled`, with notes describing the
historical event. Gated tables carry `content_flags` and `content_flags_disabled`, each holding a single flag
name (no comma lists in this dump). `NULL` and `''` both mean none.

- The row is live only if its `content_flags` flag is enabled.
- The row is live only if its `content_flags_disabled` flag is disabled.

For example, `EquestrielleCorrupted` (enabled=0) swaps two spawnentry rows. `Classic_OldWorldDrops`
(enabled=0) and `OldVP` (0) gate loot, while `OldPlane_Hate_Sky` (1), `OldPlane_Fear` (1) and
`GoingPostal` (1) are switched on. Most flag usage is on `spawn2` (751 rows reference `OldPlane_Hate_Sky`).
**Dangling flag:** `loottable` 97617 uses flag `anniversary`, which is not in `content_flags`. No NPC
references that loottable.

To get "what is live now", apply both checks at **every** gated level of a chain. For loot that means
loottable, lootdrop and lootdrop_entries; for spawns, spawn2 and spawnentry (plus `spawn2.enabled`).

```sql
-- How many spawn points / spawn entries per zone are live under World:CurrentExpansion and content_flags.
-- The expansion predicate (half-open [min, max), -1 = unbounded) is INFERRED, not verified against server code.
WITH cur AS (SELECT CAST(rule_value AS REAL) AS v FROM rule_values WHERE rule_name = 'World:CurrentExpansion'),
on_flags AS (SELECT flag_name FROM content_flags WHERE enabled = 1),
rows AS (
  SELECT s2.zone,
    s2.enabled = 1
    AND (s2.min_expansion = -1 OR s2.min_expansion <= cur.v) AND (s2.max_expansion = -1 OR s2.max_expansion > cur.v)
    AND (se.min_expansion = -1 OR se.min_expansion <= cur.v) AND (se.max_expansion = -1 OR se.max_expansion > cur.v)
    AND (COALESCE(s2.content_flags, '') = ''          OR s2.content_flags          IN (SELECT flag_name FROM on_flags))
    AND (COALESCE(s2.content_flags_disabled, '') = '' OR s2.content_flags_disabled NOT IN (SELECT flag_name FROM on_flags))
    AND (COALESCE(se.content_flags, '') = ''          OR se.content_flags          IN (SELECT flag_name FROM on_flags))
    AND (COALESCE(se.content_flags_disabled, '') = '' OR se.content_flags_disabled NOT IN (SELECT flag_name FROM on_flags))
    AS live
  FROM spawn2 s2 JOIN spawnentry se ON se.spawngroupID = s2.spawngroupID CROSS JOIN cur
)
SELECT zone, COUNT(*) AS spawn2_x_entries, SUM(live) AS live, COUNT(*) - SUM(live) AS gated_out
FROM rows
GROUP BY zone
ORDER BY gated_out DESC
LIMIT 8;
```

Under this predicate all of `bothunder` is excluded, because its spawn2 rows have a window of [4, 5) and it is
a PoP zone. The `injustice*` instances are excluded too, because their spawn2 rows have `enabled = 0`.
`kithicor` loses its pre-Hate/Sky spawn set (window [0, 0.3)) and its `content_flags_disabled =
'OldPlane_Hate_Sky'` rows.
