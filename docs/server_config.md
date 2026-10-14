# Server config tables: what's there and whether to use them

This addresses the TODO item "Decide whether server config (`data_tables.sql`: `rule_values`, `variables`,
...) is worth exposing in analysis". I checked it against dump `2026-09-27-22_44`. It ends with a
recommendation; the decision is yours.

**(inferred)** marks behaviour I took from rule names, rule notes or general EQEmu knowledge and could not
confirm, because the EQMacEmu server source isn't in this repo.

## What's there

- **`rule_sets`**: one row, `1 = default`.
- **`rule_values`** (`ruleset_id`, `rule_name`, `rule_value`, `notes`): 523 rules in ruleset 1, across 38
  categories. The large ones are `Quarm` (160), `Character` (49), `Spells` (44), `AlKabor` (36) and `World`
  (31).
  - There's one stray row in ruleset 0 (`Quarm:EnableMustEquipClickFromBags`), and no `rule_sets` row for
    id 0, so filter on `ruleset_id = 1`.
  - `zone.ruleset` is 1 in 186 zones and 0 in 6. The per-zone ruleset mechanism is effectively unused.
  - Values are stored as text (`'true'`, `'1.0'`, `'3.0'`, ...). Many rules have useful `notes`, often
    describing "AK behavior", meaning the original Al'Kabor server.
- **`variables`** (27 rows): legacy EQEmu key/value settings. It includes `EXPMod=0.75`, `AAXPMod=0.75`,
  `GroupEXPBonus=0.60`, corpse decay times, `MOTD` and `DBVersion=070_pop`.
- **`level_exp_mods`** (100 rows: `level`, `exp_mod`, `aa_exp_mod`): a related table, not necessarily from
  `data_tables.sql`, because the SQLite file doesn't record each table's source file. It holds XP modifiers
  per level. These are
  non-1.0 for levels 30–59 ("hell level" smoothing, for example 30 → 1.65, 31–34 → 0.866, 50 → 0.75,
  52 → 1.35), and `aa_exp_mod` is 1.0 everywhere.
- Content tables already carry their own modifiers: `zone.zone_exp_multiplier` (ZEM: 0.1 in four zones, 0.75 in
  two, and 1.0–1.7 elsewhere), `npc_types.exp_pct` (100 by default; 136 NPCs at 1%), `zone.reducedspawntimers`, and the era columns
  (see `docs/schema.md`).
- Other config tables, such as `command_settings` (which the README lists as part of `data_tables.sql`) and
  `logsys_categories`, are operational and irrelevant to analysis.

## Rules that matter for analysis

### Era / content (affects *which* rows count)

| Rule | Value | Effect |
|---|---|---|
| `World:CurrentExpansion` | `3.0` | Luclin. It's a REAL, which matches the fractional `min/max_expansion` values in content. This is presumably what gates content (inferred). |
| `Expansion:CurrentExpansion` | `-1` | Its note says "-1 = All". It looks like the mainline EQEmu rule left at its default, which conflicts with the rule above. It's unclear which one the server reads (inferred). |
| `Expansion:UseItemExpansionSetting` | `true` | Strips era-inappropriate item properties: focus, skill mod, bane, rec/req level. |
| `AlKabor:EnableEraItemRules` | `true` | "disable item data in the era they did not exist in". |
| `Quarm:CurrentPVPExpansion` | `4` | Era for PvP zones. |
| `Character:MaxLevel` / `MaxExpLevel` | `60` | Level cap. |

These two `CurrentExpansion` rules are the most important finding here. Any "what drops or spawns now"
analysis has to pick a current expansion value, and the dump gives two conflicting ones.

### Loot / drop rates

| Rule | Value | Would it change computed drop numbers? |
|---|---|---|
| `Zone:GlobalLootMultiplier` | `1` | No at the current value. Its note says it "Sets Global Loot drop multiplier for database based drops", which would scale everything if it were changed. |
| `Quarm:EnablePVEDoubleLoot` | `false` | No. |
| `Quarm:EnablePVERaidDoubleLoot` | `true` | **Possibly yes, for raid targets** (`npc_types.raid_target = 1`, 201 NPCs). However, its note reads "Master switch for PVP Raid Double loot", which contradicts the name. Whether PvE raid mobs drop double loot is **unresolved**. |
| `Quarm:EnablePVPDoubleLoot` | `true` | Only in PvP zones or instances. |
| `NPC:IgnoreQuestLoot` | `false` | No. |
| `SelfFound:*` | various | Only affects self-found characters. |

### Experience

| Rule / setting | Value | Would it change computed XP numbers? |
|---|---|---|
| `Character:ExpMultiplier`, `GroupExpMultiplier`, `RaidExpMultiplier`, `AAExpMultiplier` | all `1.0` | No (neutral). |
| `AlKabor:{Green,LightBlue,Blue,White,Yellow,Red}ExpMod` | `100` | No (neutral). |
| `Zone:LevelBasedEXPMods` + table `level_exp_mods` | `true` | **Yes**: per-level multipliers from 0.75 to 1.65 for levels 30–59. |
| `zone.zone_exp_multiplier` (content) | 0.1–1.7 | **Yes**, per zone. |
| `npc_types.exp_pct` (content) | 0–600% | **Yes**, per NPC. |
| `Quarm:BonusGroupEXPOverride` + `BonusGroupEXP{2..7}MemberOverride` | `true`; 1.20, 1.25, 1.35, 1.45, 1.75, 1.75 | **Yes** for group XP. These replace the AK/Velious group bonus tables (`AlKabor:VeliousGroupEXPBonuses=true`, `GroupEXPBonuses=false`, `ClassicGroupEXPBonuses=false`). The exact formula is inferred. |
| `Quarm:InstanceZEMOverride` | `45` | **Yes**, but only in instances. The unit is unclear: possibly a percentage, or ZEM ×100 (inferred). |
| `AlKabor:EnableMobLevelModifier` | `true` | **Yes**, for level 51+ (the 2002 patch's bonus for kills within ±5 levels). |
| `AlKabor:ReduceAEExp` | `true` | **Yes**, for PBAoE kills of roughly level 35–55 NPCs. |
| `Quarm:FlyingRaceExpBonus` | `0.069` | Probably a small bonus for some races. Its scope is unclear (inferred). |
| `Quarm:EnableQuestExpMultiplier` / `QuestExpMultiplier` | `true` / `1.0` | No (neutral). |
| `AlKabor:ServerExpBonus`, `Quarm:ThanksgivingExpBonus` | `false` | No. |
| `Zone:HotZoneBonus` | `0.67` | **Unverified.** `zone.hotzone = 1` in all 192 zones, so applying it literally would add +67% XP everywhere, which is implausible. The flag is probably unused or overridden in EQMacEmu (inferred). Keep it out of XP math until the code is checked. |
| `AA:ExpPerPoint` | `18750000` | Matters only for AA-per-hour estimates. |
| `variables.EXPMod` / `AAXPMod` (0.75), `GroupEXPBonus` (0.60) | legacy | **Unverified** whether the server reads them. Modern EQEmu doesn't, so I'd assume not. |

### Respawns and spawns

| Rule | Value | Effect |
|---|---|---|
| `Quarm:EnableRespawnReductionSystem` | `true` | **Yes**: it changes effective respawn times in zones with `zone.reducedspawntimers = 1` (94 zones). Respawns between about 12 s and 15 min appear to be shortened toward 12–60 s (`RespawnReductionLowerBound`=12000 ms and `HigherBound`=60000 ms, with Min/Max companions), and dungeon timers have their own bounds (360–480 s). A pull limit of 35 seems to switch it off. The exact mapping is inferred from the rule names and notes, which disagree in places (for example "500s" next to 480000). |
| `World:AdjustRespawnTimes` | `false` | `boot_respawntime` values aren't applied. |
| `Quarm:EnableQuakes` + `QuakeMinVariance`/`QuakeMaxVariance` | `true`, 604800 / 864000 s (7–10 days) | Raid-target repops come from quakes. This fits classic raid targets having `spawn2.respawntime = 1700000` (inferred). |
| `Quarm:InstanceAlwaysHasMinimumSpawnTime` / `InstanceMinimumSpawnTime` | `true` / 64800000 ms (18 h) | Minimum respawn in instances, which matches the 64800 s respawn on the `fireice` raid copies. |
| `Zone:SpawnEventMin` | `5` | Timing slack for strict `spawn_events`. |
| `Groundspawns:RandomSpawn` | `true` | Ground spawns move between random locations. |

### Skills and tradeskills (for completeness)

`Skills:SkillUpModifier=1.0`, `Quarm:GlobalSkillupDifficultyAdjustment=1.0` (with the matching `Enable...`
flag set to `false`), `Quarm:GlobalTradeSkillupDifficultyAdjustment=1.0` and `Quarm:MaxTradeskillCap=250` are
all neutral.

## Which rules change computed numbers

- **Drop rates**: none of the loot rules change single-kill drop math at current values, apart from the
  ambiguous `Quarm:EnablePVERaidDoubleLoot`. The content data (loot tables, era columns, content flags) drives
  everything else.
- **Which content is live**: `World:CurrentExpansion` (3.0) is essential, together with the `content_flags`
  table. `Expansion:CurrentExpansion` conflicts with it.
- **XP**: `level_exp_mods`, `Quarm:BonusGroupEXP*`, `InstanceZEMOverride`, `EnableMobLevelModifier` and
  `ReduceAEExp`, plus content-side ZEM and `exp_pct`.
- **Respawn timers**: the respawn-reduction system, quakes and the instance minimum spawn time all change
  the effective respawn away from `spawn2.respawntime`.

## Recommendation

**Expose a small, curated subset; don't expose the config tables wholesale.**

1. **Use `World:CurrentExpansion` (and `content_flags`) as the default era filter**, read live from
   `rule_values`. Allow overriding it, because the conflicting `Expansion:CurrentExpansion` and the unverified
   boundary semantics mean users will want "all eras" or "as of era X".
2. **Show, rather than silently apply, the drop and XP modifiers** that are currently non-neutral or
   ambiguous: `Quarm:EnablePVERaidDoubleLoot` (flag it on raid-target drops), `Zone:GlobalLootMultiplier`
   (so it's obvious if an event changes it), and the XP set (`level_exp_mods`, `Quarm:BonusGroupEXP*`,
   `Quarm:InstanceZEMOverride`). Print the raw table numbers, with these listed as known modifiers.
3. **Annotate respawn times** in NPC and zone lookups. Show `spawn2.respawntime`, and note when the zone has
   `reducedspawntimers = 1` or the NPC is a quake-cycle raid target.
4. **Leave out** `variables`, `Zone:HotZoneBonus` and the remaining roughly 500 operational rules until the
   EQMacEmu source confirms they affect gameplay math.

The things that would firm this up are reading EQMacEmu's handling of `World:CurrentExpansion`, the
expansion filter (inclusive vs exclusive `max_expansion`), `EnablePVERaidDoubleLoot`, the hotzone bonus and
the respawn-reduction formula. Until then, any effective-rate output should say that it ignores or assumes
these.

Note that a dump snapshot of `rule_values` is a published default, and the live server may differ, for
example during server events that change group bonuses or loot multipliers (inferred from the rule notes).
