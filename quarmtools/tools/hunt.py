"""Hunting grounds for a charmer: zones where a charmable pet and safe, worthwhile kills spawn together.

Used by the charm page (quarmtools/tools/charm.py), which shows them in its "Hunting grounds" view and
prints the best zones per character from ./quarm charm.

A zone is good when it has, in the same zone (charm breaks on zoning):

- a pet the character's charm spell can take, from the charm page's list of strong NPCs;
- kill targets that con above green, give experience, are beatable by that pet, and don't cost faction with
  any faction that matters;
- open ground (outdoor or mixed terrain), so a charm break can be survived by kiting.

Experience follows EQMacEmu (zone/exp.cpp, zone/mob_ai.cpp GetLevelCon):

- base experience is mob level squared * (ZEM + hot zone bonus) * 100 * npc exp_pct / 100 (Planes of Power
  era: the zone's own zone_exp_multiplier, plus the Zone:HotZoneBonus rule when zone.hotzone is set, which
  this dump does for every open zone);
- green cons give nothing; light blue gives 20%, or 60% / 80% for the top light blues above player level 40;
- for players above 50 killing NPCs above 45 within 5 levels, the "mob level modifier" multiplies experience
  (2.6 at level 59, scaled down below and adjusted by level difference at 60+);
- the level_exp_mods table multiplies by player level, and one kill is capped at 12.5% of a level;
- levels need (level - 1)^3 * 10 * race mod * level mod experience (GetEXPForLevel).

Faction follows npc_faction_entries: killing an NPC adds each entry's value to that faction. A faction
"matters" by default when merchants, bankers, guildmasters or guards belong to it (their primary faction),
unless the character is already scowling with it (PQ Companion's consider log, else race, class and deity).
"""
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from quarmtools.tools import research

# --------------------------------------------------------------------------- experience

# GetLevelCon: (highest player level, green at or below this level difference, light blue at or below).
CON_BRACKETS = [(7, -4, None), (8, -5, -4), (12, -6, -4), (16, -7, -5), (20, -8, -6), (24, -9, -7), (28, -10, -8),
                (30, -11, -9), (32, -12, -9), (36, -13, -10), (40, -14, -11), (44, -16, -12), (48, -17, -13),
                (52, -18, -14), (54, -19, -15), (56, -20, -15), (60, -21, -16), (61, -19, -14), (62, -17, -12),
                (255, -16, -11)]
CONS = ["green", "lightblue", "blue", "white", "yellow", "red"]

# GetEXPForLevel: race mod (x10 in the code), and the mod for the level being left.
RACE_EXP = {11: 95, 2: 105, 10: 115, 9: 120, 128: 120}  # halfling, barbarian, ogre, troll, iksar; others 100
LEVEL_EXP = {51: 1.5, 52: 1.6, 53: 1.7, 54: 1.9, 55: 2.1, 56: 2.3, 57: 2.5, 58: 2.7, 59: 3.0, 60: 3.0,
             61: 3.225, 62: 3.45, 63: 3.675, 64: 3.9}
KILL_CAP = 0.125
MAX_PLAYER_LEVEL = 65
MAX_MOB_LEVEL = 75


def con(player, mob):
    """The con color of a mob level to a player level."""
    diff = mob - player
    if diff == 0:
        return "white"
    if diff > 0:
        return "yellow" if diff <= 2 else "red"
    for top, green, light in CON_BRACKETS:
        if player <= top:
            if diff <= green:
                return "green"
            if light is not None and diff <= light:
                return "lightblue"
            return "blue"
    return "blue"


def level_mod(check_level):
    """GetEXPForLevel's level mod for the level being left (check_level is level - 1)."""
    if check_level <= 29:
        return 1.0
    if check_level <= 34:
        return 1.1
    if check_level <= 39:
        return 1.2
    if check_level <= 44:
        return 1.3
    if check_level <= 50:
        return 1.4
    return LEVEL_EXP.get(check_level, 3.9)


def exp_for_level(level, race=None):
    """Total experience at the start of a level."""
    check = level - 1
    return check ** 3 * 10 * RACE_EXP.get(race, 100) * level_mod(check)


def level_span(level, race=None):
    """Experience from the start of this level to the next."""
    return exp_for_level(level + 1, race) - exp_for_level(level, race)


def kill_multiplier(player, mob):
    """Light blue and mob level multipliers for a solo kill (0 for green), before ZEM and level mods."""
    c = con(player, mob)
    if c == "green":
        return 0.0
    diff = mob - player
    if mob > 45 and player > 50 and -6 < diff < 6:
        if diff and player > 59:
            return min((260 - 13 * (player - mob)) / 100, 3.0)
        if player < 60:
            x = 60 - player
            cap, adj = 2.6 - x * 0.16, 0.13 - x * 0.013
            if mob > player:
                adj /= 2
            return cap - (player - mob) * adj
        return 2.6
    if c == "lightblue":
        if player > 40:
            if con(player, mob - 4) == "lightblue":
                return 0.8
            if con(player, mob - 3) == "lightblue":
                return 0.6
        return 0.2
    return 1.0


def kill_fraction(player, mob, zem, exp_pct=100, race=None, hell_mod=1.0, class_id=None):
    """Fraction of the player's current level one solo kill gives (capped at 12.5%)."""
    mult = kill_multiplier(player, mob)
    if not mult or exp_pct <= 0:
        return 0.0
    class_mult = 10 / 9 if class_id == 1 else 10 / 9.05 if class_id == 9 else 1.0
    exp = mob * mob * zem * 100 * exp_pct / 100 * mult * hell_mod * class_mult
    return min(exp / level_span(player, race), KILL_CAP)


def xp_tables(conn):
    """What the page needs to do the same arithmetic: multipliers by player and mob level, level spans
    (race mod 100) and the level_exp_mods table."""
    hell = {lvl: mod for lvl, mod in conn.execute("SELECT level, exp_mod FROM level_exp_mods") if mod}
    return {
        "mult": {p: [round(kill_multiplier(p, m), 4) for m in range(MAX_MOB_LEVEL + 1)]
                 for p in range(1, MAX_PLAYER_LEVEL + 1)},
        "span": {p: level_span(p) for p in range(1, MAX_PLAYER_LEVEL + 1)},
        "hell": hell, "cap": KILL_CAP, "race_exp": RACE_EXP, "max_level": MAX_PLAYER_LEVEL,
        "cons": {p: [CONS.index(con(p, m)) for m in range(MAX_MOB_LEVEL + 1)] for p in range(1, MAX_PLAYER_LEVEL + 1)},
    }


# --------------------------------------------------------------------------- factions

# Placeholder factions that merchants and guards sometimes carry, and that nobody cares about.
JUNK_FACTION = re.compile(r"^(kos\b|indifferent$|dubiously$|beta neutral$|none$)", re.I)
GUARD_NAME = re.compile(r"^#?(guard|guardian|sentinel|captain|lieutenant|sergeant|warder|deputy|trooper)", re.I)
NON_KILL_CLASSES = set(range(20, 35)) | {40, 41, 59}
SCOWLING = -751
BUCKET_SCOWLING = {"scowling", "threatening"}  # threatening is close enough: guards attack at scowling


def load_factions(conn):
    """{"names", "sets", "matters", "base", "mods"}: faction names, kill hits per npc_faction_id
    (nonzero only), the default "matters" set and the inputs for a character's starting standing."""
    names = dict(conn.execute("SELECT id, name FROM faction_list"))
    sets = defaultdict(list)
    for nfid, fid, value in conn.execute(
            "SELECT npc_faction_id, faction_id, value FROM npc_faction_entries WHERE value != 0 ORDER BY sort_order"):
        sets[nfid].append([fid, value])
    matters = set()
    for raw, cls, fid in conn.execute("""
            SELECT n.name, n.class, nf.primaryfaction FROM npc_types n JOIN npc_faction nf ON nf.id = n.npc_faction_id
            WHERE nf.primaryfaction > 0 AND EXISTS (SELECT 1 FROM spawnentry se WHERE se.npcID = n.id)"""):
        if (cls in NON_KILL_CLASSES or GUARD_NAME.match(raw or "")) and not JUNK_FACTION.match(names.get(fid, "")):
            matters.add(fid)
    base = dict(conn.execute("SELECT id, base FROM faction_list"))
    mods = defaultdict(dict)
    for fid, mod, name in conn.execute("SELECT faction_id, mod, mod_name FROM faction_list_mod"):
        mods[fid][name] = mod
    return {"names": names, "sets": dict(sets), "matters": matters, "base": base, "mods": mods}


def starting_standing(factions, fid, race, class_id, deity):
    """Faction value for a fresh character of this race, class and deity (before any kills or quests)."""
    m = factions["mods"].get(fid, {})
    return (factions["base"].get(fid, 0) + m.get(f"r{race}", 0) + m.get(f"c{class_id}", 0)
            + m.get(f"d{deity}", 0))


def read_quarmy(path):
    """{"race", "class", "deity", "level"} from a Zeal Quarmy export, or {} if missing or unreadable."""
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            f = line.split("\t")
            if len(f) > 7 and f[0] == "Character" and f[1] != "Name":
                return {"level": int(f[3]), "class": int(f[4]), "race": int(f[5]), "deity": int(f[7])}
    except (OSError, ValueError):
        pass
    return {}


def pqc_buckets(path, name):
    """{faction_id: last consider bucket} for a character from PQ Companion, skipping suspect readings."""
    try:
        conn = research.open_readonly(path)
    except research.ResearchError:
        return {}
    try:
        return {fid: bucket for fid, bucket in conn.execute("""
            SELECT t.faction_id, t.last_bucket FROM character_faction_tally t JOIN characters c ON c.id = t.character_id
            WHERE c.name = ? COLLATE NOCASE AND t.last_bucket != '' AND t.last_consider_suspect = 0""", (name,))}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def already_scowling(factions, character, buckets):
    """({faction: reason} the character is already scowling or threatening with by PQ Companion's last
    consider, so more hits change nothing; {faction: reason} it would start scowling with by race, class
    and deity). Only the first switches a faction off by default: quests and kills since character
    creation (Claws of Veeshan for Skyshrine, say) are invisible to the second."""
    known, starts = {}, {}
    for fid in factions["matters"]:
        bucket = buckets.get(fid)
        if bucket in BUCKET_SCOWLING:
            known[fid] = f"considered {bucket}"
        elif not bucket and character and starting_standing(
                factions, fid, character["race"], character["class"], character["deity"]) <= SCOWLING:
            starts[fid] = "starts scowling (race, class or deity)"
    return known, starts


def target_faction(sets, matters, nfid):
    """(hits on factions that matter, other hits, gains) for killing an NPC with this npc_faction_id."""
    bad, other, good = [], [], []
    for fid, value in sets.get(nfid, []):
        (good if value > 0 else bad if fid in matters else other).append((fid, value))
    return bad, other, good


# --------------------------------------------------------------------------- kill targets

SA_SUMMON, SA_BANE_ONLY, SA_MELEE_IMMUNE, SA_MAGIC_ONLY, SA_AGGRO_IMMUNE, SA_PLAYER_IMMUNE = 1, 9, 19, 23, 24, 35
# Abilities that make an NPC unkillable by a pet (boats, triggers, scenery).
UNHITTABLE = {SA_BANE_ONLY, SA_MELEE_IMMUNE, SA_AGGRO_IMMUNE, SA_PLAYER_IMMUNE}


def kill_targets(npcs, junk_bodies):
    """Every NPC worth listing as something to kill: spawns live, not a raid target, merchant or junk body,
    gives experience and can be hit by a pet. Page rows: compact keys, live zones only."""
    rows = []
    for n in npcs:
        live = [s for s in n["spawns"] if s["live"]]
        if not live or n["raid"] or n["body"] in junk_bodies or n["class_id"] in NON_KILL_CLASSES:
            continue
        if n["exp_pct"] <= 0 or n["hp"] <= 1 or not n["name"].strip() or UNHITTABLE & n["abilities"].keys():
            continue
        flags = (1 if SA_SUMMON in n["abilities"] else 0) | (2 if SA_MAGIC_ONLY in n["abilities"] else 0)
        rows.append({
            "id": n["id"], "n": n["name"], "l": n["level"], "hp": n["hp"], "dps": round(n["dps"], 1),
            "rg": n["hp_regen"], "f": n["faction_id"], "x": n["exp_pct"], "fl": flags,
            "z": [[s["zone"], s["points"], s["respawn"], s["chance"]] for s in live],
        })
    rows.sort(key=lambda r: (r["l"], r["n"]))
    return rows


# Planes of Power zones that need progression flags; the zone table's flag_needed only marks two of them.
FLAG_ZONES = set("""povalor postorms bothunder hohonora hohonorb codecay potactics potorment solrotower
                    poair poeartha poearthb pofire powater potimea potimeb""".split())


def hot_zone_bonus(conn):
    """The Zone:HotZoneBonus rule (added to a hot zone's ZEM), or 0 if it isn't set."""
    try:
        row = conn.execute("SELECT rule_value FROM rule_values WHERE rule_name = 'Zone:HotZoneBonus' "
                           "ORDER BY ruleset_id LIMIT 1").fetchone()
        return float(row[0]) if row else 0.0
    except (sqlite3.Error, ValueError):
        return 0.0


def zone_info(conn, terrain_of):
    """{short name: [long name, terrain, experience multiplier, expansion, needs a flag, ZEM, hot]} for zones
    with a zone row. The multiplier is the ZEM plus the hot zone bonus in hot zones."""
    bonus = hot_zone_bonus(conn)
    out = {}
    for short, name, zem, era, flag, hot in conn.execute(
            "SELECT short_name, long_name, zone_exp_multiplier, expansion, flag_needed, hotzone FROM zone"):
        zem = zem if zem is not None and zem >= 0 else 0.75
        out[short] = [name or short, terrain_of(short), round(zem + (bonus if hot else 0), 4), era,
                      bool(flag and str(flag).strip()) or short in FLAG_ZONES, zem, bool(hot)]
    return out


# --------------------------------------------------------------------------- ranking
# The page (charm.html) does the same arithmetic in JavaScript; keep the two in step.

OVERHEAD = 30  # seconds per kill for pulling, walking and charm upkeep
SURVIVE = 0.6  # a pet may lose at most this share of its HP in one fight
PETS_PER_ZONE = 20  # the strongest pets (by DPS) tried in each zone
OPEN_TERRAIN = ("outdoor", "mixed")


def fight(pet, target):
    """(seconds to kill, pet HP lost) for a pet against a target, ignoring misses and mitigation."""
    if pet["dps"] <= 0:
        return None
    ttk = target["hp"] / pet["dps"]
    return ttk, target["dps"] * ttk


def kills_per_hour(spawn):
    """How often a target spawns in a zone: points * chance / respawn (an hour's worth without a respawn)."""
    _, points, respawn, chance = spawn
    return points * (chance or 100) / 100 * (3600 / respawn if respawn else 1)


def zone_plan(pet, targets, zone, zem, player, race=None, hell=1.0, matters=frozenset(), sets=None,
              overhead=OVERHEAD):
    """Estimated fraction of a level per hour for a pet on the given targets in a zone, and the kills used.

    Targets are taken greedily by experience per second of cycle time (fight, then the pet regenerating
    what it lost, then overhead), each up to how often it spawns.
    """
    options = []
    for t in targets:
        spawn = next((z for z in t["z"] if z[0] == zone), None)
        if spawn is None or target_faction(sets or {}, matters, t["f"])[0]:
            continue
        frac = kill_fraction(player, t["l"], zem, t["x"], race, hell)
        result = fight(pet, t)
        if not frac or result is None:
            continue
        ttk, lost = result
        if lost > SURVIVE * pet["hp"]:
            continue
        cycle = ttk + lost / (max(pet.get("rg", 0), 1) / 6) + overhead
        options.append((frac / cycle, frac, cycle, kills_per_hour(spawn), t))
    options.sort(key=lambda o: -o[0])
    budget, total, used = 3600.0, 0.0, []
    for _, frac, cycle, per_hour, t in options:
        if budget <= 0:
            break
        kills = min(per_hour, budget / cycle)
        budget -= kills * cycle
        total += kills * frac
        used.append((t, kills, frac))
    return total, used


def charmable(spell, row, target_bodies):
    bodies = target_bodies.get(spell["targettype"])
    return (bodies is None or row["b"] in bodies) and row["l"] <= spell["cap"]


def rank_zones(data, player, spell, race=None, matters=None, terrains=OPEN_TERRAIN, min_score=1.0):
    """[(fraction of a level per hour, zone, pet row, kills used)] best first, from the page payload."""
    h = data["hunt"]
    matters = set(h["matters"]) if matters is None else matters
    hell = h["xp"]["hell"].get(player, 1.0)
    pets = defaultdict(list)
    for r in data["npcs"]:
        if r["raid"] or r["s"] < min_score or not charmable(spell, r, data["target_bodies"]):
            continue
        for z in r["z"]:
            if z[6]:
                pets[z[0]].append(r)
    targets = defaultdict(list)
    for t in h["targets"]:
        for z in t["z"]:
            targets[z[0]].append(t)
    out = []
    for zone, zone_pets in pets.items():
        info = h["zones"].get(zone)
        if not info or info[1] not in terrains:
            continue
        best = None
        for p in sorted(zone_pets, key=lambda r: -r["dps"])[:PETS_PER_ZONE]:
            total, used = zone_plan(p, targets[zone], zone, info[2], player, race, hell, matters, h["sets"])
            if total > 0 and (best is None or total > best[0]):
                best = (total, zone, p, used)
        if best:
            out.append(best)
    out.sort(key=lambda b: -b[0])
    return out
