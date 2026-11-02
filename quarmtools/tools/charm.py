"""Find NPCs worth charming: ones far stronger than other NPCs of their level, within your charm cap.

Usage: ./quarm charm [--db FILE] [--eq-dir DIR] [--pqc-db FILE] [--era N] [--out FILE] [--csv FILE]

The front end (./quarm) serves the page at /charm, rebuilt on every load. From the command line it
writes one self-contained HTML file and a CSV of every charmable NPC with its scores.

Characters come from the same Zeal spellbook exports as the research page (/outputfile spellbook):
class from the book, level from PQ Companion's user.db, and the charm spells actually in the book.
There are also "New <class>" entries for each charm class (Enchanter, Necromancer, Druid, Bard).

What makes an NPC charmable here follows the server code (EQMacEmu zone/spells.cpp):

- its level is at or below the charm spell's cap (the spell effect's max value);
- the spell's target type allows its body type: undead charms need Undead, Summoned Undead or
  Vampire; animal charms need Animal; plant charms need Plant; single-target charms take any;
- it isn't flagged immune to charm (special ability 14), isn't a merchant, banker or guildmaster,
  and isn't a level 50+ NPC in Veksar. NPCs immune to magic (20) or to players (35) are left out too.

The charmed pet keeps the NPC's own stats (the database has no separate charm stats). Its melee
damage per second is estimated from the code charmed pets run (zone/mob_ai.cpp):

- each round is attack_count hits (at least 1), plus a double attack chance from the NPC's skill
  (level * 5, capped at 210, or 250 above level 50; plus its level above level 35; out of 500);
- warriors and monks of level 60+ get a 13.5% triple attack after a double;
- the dual wield special ability adds an offhand round (NPC offhand weapons from loot aren't counted);
- average hit is the midpoint of min and max damage, and attack_delay is in tenths of a second;
- charmed pets never flurry or rampage, so those don't count; kicks and bashes and misses are ignored.

Power is relative to level: each NPC's DPS, hit points and AC are divided by the median of ordinary
NPCs of the same level (ones that spawn, aren't raid targets, merchants or untargetable), and the
score is the weighted geometric mean of those ratios (DPS 50%, HP 30%, AC 20%), each capped at 5x. A score of 1 is a
typical mob for its level; Ryklo Rikes in The Dawnshroud Peaks scores far above that.

Each spawn zone is classified by hand as outdoor, mixed (partly open), city or dungeon
(TERRAIN_ZONES), since a charm break is far easier to survive in the open. The page ranks outdoor
NPCs first by default and can show outdoor zones only.
"""
import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from quarmtools import common, web
from quarmtools.tools import research

NAME = "charm"
SUMMARY = "NPCs worth charming: unusually strong mobs for their level, within your charm spell's cap."

DEFAULT_DB = common.DEFAULT_DB
DEFAULT_OUT = common.DATA_DIR / "charm_targets.html"
DEFAULT_CSV = common.DATA_DIR / "charm_targets.csv"
TEMPLATE = Path(__file__).resolve().with_suffix(".html")
DEFAULT_ERA = research.DEFAULT_ERA

CHARM_EFFECT = 22
CHARM_CLASSES = {14: "Enchanter", 11: "Necromancer", 6: "Druid", 8: "Bard"}
# Spell target type -> body types it can charm (None = any).
UNDEAD_BODIES = {3, 8, 12}
TARGET_BODIES = {5: None, 9: {21}, 10: UNDEAD_BODIES, 16: {25}}
TARGET_LABELS = {5: "any NPC", 9: "animals", 10: "undead", 16: "plants"}
# Never charm targets: guildmasters (20-34), bankers, merchants.
IMMUNE_CLASSES = set(range(20, 35)) | {40, 41, 59}
# Untargetable, trigger and swarm-pet body types: not real mobs.
JUNK_BODIES = {11, 60, 63, 65, 66, 67}
VEKSAR = "veksar"

# Where a charm break is survivable: outdoors you can run, gate or root in the open; in a dungeon a broken
# pet pulls its neighbours. The database has no reliable outdoor flag (zone.sky and castoutdoor mark
# Blackburrow as outdoor and The Dawnshroud Peaks as not), so zones are classified by hand.
# "mixed" is partly open: castle grounds, open ruins, an outdoor city of giants.
TERRAIN_ZONES = {
    "outdoor": """
        airplane air_instanced arena beholder butcher cauldron commons ecommons eastkarana erudsxing everfrost
        feerrott gfaydark innothule kerraridge kithicor lakerathe lavastorm lfaydark misty nektulos northkarana nro
        oasis oot qey2hh1 qeytoqrg rathemtn southkarana sro steamfont tox
        burningwood dreadlands emeraldjungle fieldofbone firiona frontiermtns lakeofillomen overthere skyfire
        stonebrunt swampofnohope timorous towerbone trakanon warslikswood
        cobaltscar eastwastes iceclad mischiefplane westwastes
        dawnshroud fearplane fear_instanced greatdivide grimling growthplane hollowshade jaggedpine letalis maiden
        mseru scarlet shadeweaver tenebrous thegrey twilight umbral wakening
        poair poeartha poearthb pofire podisease ponightmare postorms potimea povalor""",
    "mixed": """
        citymist crushbone highpass kael karnor mistmoore skyshrine unrest bothunder hohonora""",
    "city": """
        akanon bazaar cabeast cabwest cshome erudnext erudnint felwithea felwitheb freporte freportn freportw grobb
        halas kaladima kaladimb katta neriaka neriakb neriakc nexus oggok paineel poknowledge potranquility qeynos
        qeynos2 qrg rivervale sharvahl shadowhaven soltemple sseru thurgadina""",
    "dungeon": """
        befallen blackburrow cazicthule cazicthule_old codecay guktop gukbottom highkeep hole hole_instanced najena
        paw permafrost qcat runnyeye soldunga soldungb
        chardok dalnir droga kaesora kurn nurga sebilis towerfrost veeshan warrens
        crystal frozenshadow necropolis sirens sleeper templeveeshan thurgadinb veksar velketor
        acrylia akheva charasis echo fungusgrove griegsend hateplane hate_instanced kedge netherbian paludal
        ssratemple thedeep vexthal
        hohonorb nightmareb pojustice poinnovation potactics potimeb potorment powater solrotower""",
}
ZONE_TERRAIN = {z: t for t, zones in TERRAIN_ZONES.items() for z in zones.split()}
# Lower is better for surviving a charm break. Unclassified zones sort with cities.
TERRAIN_RANK = {"outdoor": 0, "mixed": 1, "city": 2, "unknown": 2, "dungeon": 3}


# Spawn zones with no zone row: instanced copies of a zone and a few one-offs.
ZONE_ALIASES = {"mischiefhouse": "hateplane", "iceclad2": "iceclad", "cshome2": "cshome"}


def zone_terrain(zone):
    """The zone's terrain. Instanced copies ("chardok_instanced", "injustice3") take their base zone's."""
    if zone in ZONE_TERRAIN:
        return ZONE_TERRAIN[zone]
    base = re.sub(r"_instanced$", "", zone)
    base = ZONE_ALIASES.get(base, base)
    if base not in ZONE_TERRAIN and base.startswith("injustice"):
        base = "pojustice"
    return ZONE_TERRAIN.get(base, "unknown")


def best_terrain(zones, live_only=False):
    """The most open terrain among an NPC's spawn zones (page rows: z[0] = zone, z[6] = live)."""
    terrains = [zone_terrain(z[0]) for z in zones if z[6] or not live_only]
    return min(terrains, key=TERRAIN_RANK.get, default="unknown")
WEIGHTS = {"dps": 0.5, "hp": 0.3, "ac": 0.2}
# Pool neighbouring levels into a level's baseline until it has this many NPCs.
BASELINE_MIN = 30
DEFAULT_DELAY = 30
# The page (and the command-line summary) start at this many levels below the charm cap.
LEVEL_WINDOW = 15

SA_NAMES = {1: "summon", 2: "enrage", 3: "rampage", 4: "AE rampage", 5: "flurry", 6: "triple", 7: "dual wield",
            9: "bane only", 10: "magical", 11: "ranged", 12: "unslowable", 13: "unmezzable", 15: "unstunnable",
            16: "unsnareable", 17: "unfearable", 18: "undispellable", 19: "melee immune", 20: "magic immune",
            21: "won't flee", 23: "magic weapons only", 31: "unpacifiable", 32: "leash", 33: "tether"}
SA_DUAL_WIELD = 7
SA_CHARM_IMMUNE = 14
SA_MAGIC_IMMUNE = 20
SA_HARM_FROM_CLIENT_IMMUNE = 35
# No single stat's ratio counts for more than this, so a level 1 NPC with 30,000 HP doesn't top the list.
RATIO_CAP = 5.0

NPC_CLASSES = {**research.CLASS_NAMES, 20: "Guildmaster", 40: "Banker", 41: "Merchant"}
BODY_NAMES = {0: "", 1: "Humanoid", 2: "Lycanthrope", 3: "Undead", 4: "Giant", 5: "Construct", 6: "Extraplanar",
              7: "Magical", 8: "Summoned undead", 9: "Bane giant", 10: "Dain", 12: "Vampire", 13: "Atenha Ra",
              14: "Greater Akheva", 15: "Khati Sha", 16: "Seru", 18: "Draz Nurakk", 19: "Zek", 20: "Luggald",
              21: "Animal", 22: "Insect", 23: "Monster", 24: "Summoned", 25: "Plant", 26: "Dragon",
              27: "Summoned 2", 28: "Summoned 3", 29: "Dragon 2", 30: "Velious dragon", 32: "Dragon 3",
              33: "Boxes", 34: "Muramite"}
PQDI_NPC = "https://www.pqdi.cc/npc/{}"

CSV_FIELDS = ["npc_id", "name", "level", "maxlevel", "class", "body", "terrain", "score", "dps", "dps_ratio", "hp", "hp_ratio",
              "ac", "ac_ratio", "hp_regen", "mr", "min_hit", "max_hit", "delay", "hits_per_round", "abilities",
              "raid_target", "zones", "pqdi_url"]


# ---------------------------------------------------------------------------- mechanics


def parse_special_abilities(text):
    """{ability id: [params]} for abilities that are on ("14,1^10,1" -> {14: [1], 10: [1]})."""
    out = {}
    for part in (text or "").split("^"):
        fields = [f.strip() for f in part.split(",")]
        try:
            code, value = int(fields[0]), int(fields[1])
        except (IndexError, ValueError):
            continue
        if value:
            params = []
            for f in fields[2:]:
                try:
                    params.append(int(f))
                except ValueError:
                    params.append(0)
            out[code] = [value] + params
    return out


def npc_skill(level):
    """An NPC's double attack and dual wield skill (zone/npc.cpp), before the level bonus."""
    if level <= 7:
        return 0
    return 250 if level > 50 else min(level * 5, 210)


def double_attack_chance(level):
    skill = npc_skill(level)
    if skill and level > 35:
        skill += level
    return min(skill / 500, 1.0)


def dual_wield_chance(level):
    skill = npc_skill(level)
    if skill and level > 35:
        skill += level
    return min(skill / 375, 1.0)


def hits_per_round(level, npc_class, attack_count, abilities):
    """Expected melee hits per attack round for a charmed NPC (see the module docstring)."""
    da = double_attack_chance(level)
    hits = max(attack_count or 0, 1) + da
    if npc_class in (1, 7) and level >= 60:
        hits += da * 0.135
    if SA_DUAL_WIELD in abilities:
        offhand = 1 + (da if npc_skill(level) >= 150 else 0)
        hits += dual_wield_chance(level) * offhand
    return hits


def melee_dps(npc):
    delay = (npc["attack_delay"] if npc["attack_delay"] and npc["attack_delay"] > 0 else DEFAULT_DELAY) / 10
    avg_hit = (max(npc["mindmg"], 0) + max(npc["maxdmg"], npc["mindmg"], 0)) / 2
    return avg_hit * npc["hits"] / delay


def charm_immunity(npc):
    """Why the NPC can never be charmed, or "" if it can."""
    if SA_CHARM_IMMUNE in npc["abilities"]:
        return "immune to charm"
    if SA_MAGIC_IMMUNE in npc["abilities"]:
        return "immune to magic"
    if SA_HARM_FROM_CLIENT_IMMUNE in npc["abilities"]:
        return "immune to players"
    if not npc["name"]:
        return "unnamed placeholder"
    if npc["class_id"] in IMMUNE_CLASSES:
        return NPC_CLASSES.get(npc["class_id"], "merchant/banker/guildmaster")
    zones = {s["zone"] for s in npc["spawns"]}
    if zones and zones <= {VEKSAR} and npc["level"] >= 50:
        return "Veksar, level 50+"
    return ""


def can_charm(spell, npc):
    """Whether this charm spell can take this NPC (ignoring resists)."""
    if npc["immune"]:
        return False
    bodies = TARGET_BODIES.get(spell["targettype"])
    if bodies is not None and npc["body"] not in bodies:
        return False
    return npc["level"] <= spell["cap"]


# ---------------------------------------------------------------------------- database


def load_charm_spells(conn):
    """Player charm spells: [{id, name, targettype, cap, duration, resist_diff, scroll, levels}].

    levels is {class id: level} for the charm classes that get it. Spells with no scroll item
    (e.g. Undead Pact, an item click) are kept but flagged, and are only used when in a spellbook.
    """
    cols = ", ".join(f"classes{c}" for c in CHARM_CLASSES)
    rows = conn.execute(f"""
        SELECT id, name, targettype, buffduration, ResistDiff,
               effectid1, max1, effectid2, max2, effectid3, max3, {cols}
        FROM spells_new
        WHERE {CHARM_EFFECT} IN (effectid1, effectid2, effectid3)""").fetchall()
    scrolls = research.load_scroll_items(conn, [r[0] for r in rows])
    song_ids = {sid for (sid,) in conn.execute(
        "SELECT scrolleffect FROM items WHERE Name LIKE 'Song:%' AND scrolleffect > 0")}
    out = []
    for r in rows:
        sid, name, ttype, duration, resist_diff = r[:5]
        cap = next(m for e, m in zip(r[5:11:2], r[6:11:2]) if e == CHARM_EFFECT)
        levels = {c: lvl for c, lvl in zip(CHARM_CLASSES, r[11:]) if lvl is not None and lvl < 255}
        if not levels or ttype not in TARGET_BODIES:
            continue
        out.append({"id": sid, "name": name, "targettype": ttype, "cap": cap or 0, "duration": duration or 0,
                    "resist_diff": resist_diff or 0, "scroll": bool(scrolls.get(sid)) or sid in song_ids,
                    "levels": levels})
    return sorted(out, key=lambda s: (min(s["levels"].values()), s["name"]))


def load_npcs(conn, era):
    """Every NPC with its stats, abilities and spawn points (zone, chance, respawn, live in era)."""
    flags = research.enabled_content_flags(conn)
    npcs = {}
    for row in conn.execute("""
            SELECT id, name, level, maxlevel, class, race, bodytype, hp, mindmg, maxdmg, attack_count,
                   attack_delay, AC, MR, hp_regen_rate, special_abilities, npc_spells_id, raid_target,
                   rare_spawn, see_invis, runspeed
            FROM npc_types"""):
        (nid, name, level, maxlevel, cls, race, body, hp, mindmg, maxdmg, attack_count, delay, ac, mr, regen,
         special, spells_id, raid, rare, see_invis, runspeed) = row
        npcs[nid] = {
            "id": nid, "name": research.npc_display_name(name or ""), "raw_name": name or "",
            "level": level or 0, "maxlevel": max(maxlevel or 0, level or 0), "class_id": cls or 0, "race": race,
            "body": body or 0, "hp": hp or 0, "mindmg": mindmg or 0, "maxdmg": maxdmg or 0,
            "attack_count": attack_count or 0, "attack_delay": delay or 0, "ac": ac or 0, "mr": mr or 0,
            "hp_regen": regen or 0, "abilities": parse_special_abilities(special), "caster": bool(spells_id),
            "raid": bool(raid), "rare": bool(rare), "see_invis": bool(see_invis), "runspeed": runspeed or 0,
            "spawns": [],
        }
    by_zone = {}
    for row in conn.execute("""
            SELECT se.npcID, s2.zone, COALESCE(z.long_name, s2.zone), z.expansion, se.chance,
                   (SELECT SUM(chance) FROM spawnentry t WHERE t.spawngroupID = se.spawngroupID),
                   s2.respawntime, s2.enabled,
                   se.min_expansion, se.max_expansion, se.content_flags, se.content_flags_disabled,
                   s2.min_expansion, s2.max_expansion, s2.content_flags, s2.content_flags_disabled,
                   z.min_expansion, z.max_expansion, z.content_flags, z.content_flags_disabled
            FROM spawnentry se
            JOIN spawn2 s2 ON s2.spawngroupID = se.spawngroupID
            LEFT JOIN zone z ON z.short_name = s2.zone
            WHERE s2.zone IS NOT NULL AND s2.zone != ''"""):
        nid, zone, zone_name, zone_era, chance, total, respawn, enabled = row[:8]
        npc = npcs.get(nid)
        if npc is None or not chance:
            continue
        live = bool(enabled) and (zone_era is None or zone_era <= era) and \
            all(research.live_in_era(row[k:k + 4], era, flags) for k in (8, 12, 16))
        pct = 100 * chance / total if total else 0
        key = (nid, zone)
        s = by_zone.get(key)
        if s is None:
            s = by_zone[key] = {"zone": zone, "zone_name": zone_name, "expansion": zone_era, "points": 0,
                                "chance": 0, "respawn": None, "live": False}
            npc["spawns"].append(s)
        s["points"] += 1
        s["chance"] = max(s["chance"], round(pct, 1))
        if respawn and (s["respawn"] is None or respawn < s["respawn"]):
            s["respawn"] = respawn
        s["live"] = s["live"] or live
    for npc in npcs.values():
        npc["spawns"].sort(key=lambda s: (not s["live"], s["zone_name"]))
    return npcs


# ---------------------------------------------------------------------------- scoring


def is_ordinary(npc):
    """Whether an NPC belongs in the per-level baseline."""
    return (npc["spawns"] and not npc["raid"] and npc["body"] not in JUNK_BODIES
            and npc["class_id"] not in IMMUNE_CLASSES and npc["level"] > 0)


def level_baselines(npcs):
    """level -> {"dps", "hp", "ac"} medians of ordinary NPCs, pooling neighbouring levels when few."""
    by_level = defaultdict(list)
    for n in npcs:
        if is_ordinary(n):
            by_level[n["level"]].append(n)
    out = {}
    for level in range(1, max(by_level, default=0) + 1):
        pool, width = list(by_level.get(level, [])), 0
        while len(pool) < BASELINE_MIN and width < 10:
            width += 1
            pool += by_level.get(level - width, []) + by_level.get(level + width, [])
        if pool:
            out[level] = {k: statistics.median(n[k] for n in pool) for k in ("dps", "hp", "ac")}
    return out


def ratio(value, base):
    return value / base if base and base > 0 else (1.0 if not value else 10.0)


def score_npcs(npcs):
    """Adds hits, dps, immune, the ratios to the level's median and the combined score to each NPC."""
    for n in npcs:
        n["hits"] = hits_per_round(n["level"], n["class_id"], n["attack_count"], n["abilities"])
        n["dps"] = melee_dps(n)
        n["immune"] = charm_immunity(n)
    baselines = level_baselines(npcs)
    for n in npcs:
        base = baselines.get(n["level"], {})
        score = 1.0
        for key, weight in WEIGHTS.items():
            r = ratio(n[key], base.get(key))
            n[f"{key}_ratio"] = r
            score *= min(max(r, 0.01), RATIO_CAP) ** weight
        n["score"] = score
    return baselines


# ---------------------------------------------------------------------------- characters


def best_spell(spells, class_id, level, book=None):
    """The default charm spell for a character: the highest cap among the usable ones.

    With a spellbook, only spells in it count. Without one (a new character), spells need a
    scroll and a class level at or below level. Unresistable short charms (Dictate, Enslave
    Death: resist adjustment -1000) are only picked if nothing else is available.
    """
    usable = [s for s in spells if class_id in s["levels"] and
              (s["id"] in book if book is not None else s["scroll"] and s["levels"][class_id] <= level)]
    if not usable:
        return None
    normal = [s for s in usable if s["resist_diff"] > -1000] or usable
    return max(normal, key=lambda s: (s["cap"], s["levels"][class_id]))["id"]


def characters(spells, eq_dir, pqc_db, conn):
    """Charm-class characters from the spellbook exports, plus a "New <class>" entry per class."""
    books = {}
    if eq_dir and eq_dir.is_dir():
        for path in sorted(eq_dir.glob("*-Spellbook.txt")):
            books[path.name[:-len("-Spellbook.txt")]] = research.parse_spellbook(path)
    class_levels = research.load_class_levels(conn, {s for b in books.values() for s in b})
    levels = research.pqc_levels(pqc_db)
    out, skipped = [], []
    for name, book in books.items():
        cls = research.infer_class(book, class_levels)
        if cls not in CHARM_CLASSES:
            skipped.append(f"{name} ({research.CLASS_NAMES.get(cls, 'unknown class')})")
            continue
        level = levels.get(name.lower()) or max(book.values(), default=1)
        in_book = sorted(s["id"] for s in spells if s["id"] in book)
        out.append({"key": name, "name": name, "class_id": cls, "level": level, "known": in_book,
                    "spell": best_spell(spells, cls, level, set(book))})
    for cls in CHARM_CLASSES:
        out.append({"key": f"new-{cls}", "name": f"New {CHARM_CLASSES[cls]}", "class_id": cls, "level": 60,
                    "new": True, "known": None, "spell": best_spell(spells, cls, 60)})
    return out, skipped


# ---------------------------------------------------------------------------- gather and output


def candidate(n):
    """The fields the page needs for one NPC (compact keys keep the page small)."""
    tags = [SA_NAMES[a] for a in sorted(n["abilities"]) if a in SA_NAMES]
    return {
        "id": n["id"], "n": n["name"], "l": n["level"], "ml": n["maxlevel"], "c": n["class_id"], "b": n["body"],
        "hp": n["hp"], "ac": n["ac"], "mr": n["mr"], "rg": n["hp_regen"], "mn": n["mindmg"], "mx": n["maxdmg"],
        "dl": n["attack_delay"] or DEFAULT_DELAY, "h": round(n["hits"], 2), "dps": round(n["dps"], 1),
        "rd": round(n["dps_ratio"], 2), "rh": round(n["hp_ratio"], 2), "ra": round(n["ac_ratio"], 2),
        "s": round(n["score"], 2), "t": tags, "cs": n["caster"], "raid": n["raid"],
        "named": is_named(n["raw_name"]) or n["rare"],
        "z": [[s["zone"], s["zone_name"], s["expansion"], s["points"], s["chance"], s["respawn"], s["live"],
               zone_terrain(s["zone"])] for s in n["spawns"]],
    }


def merge_copies(rows):
    """One row per NPC that the dump repeats under several ids with the same stats and zones.

    The lowest id is kept; "ids" lists the others (for spawns, the zones are merged).
    """
    merged = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        key = (r["n"], r["l"], r["ml"], r["c"], r["b"], r["hp"], r["ac"], r["mn"], r["mx"], r["dl"], r["h"],
               tuple(r["t"]), r["raid"], frozenset(z[0] for z in r["z"]))
        m = merged.get(key)
        if m is None:
            merged[key] = {**r, "ids": []}
            continue
        m["ids"].append(r["id"])
        zones = {z[0]: z for z in m["z"]}
        for z in r["z"]:
            old = zones[z[0]]
            old[3] += z[3]
            old[4] = max(old[4], z[4])
            old[5] = min(x for x in (old[5], z[5]) if x) if old[5] or z[5] else None
            old[6] = old[6] or z[6]
    return list(merged.values())


def is_named(raw_name):
    """EQ's named mobs have capitalized names ("Ryklo_Rikes"); ordinary ones start "a_" or "an_"."""
    name = raw_name.lstrip("#")
    return bool(name) and name[0].isupper() and not name.lower().startswith(("a_", "an_", "the_"))


def gather(db_path, eq_dir, pqc_db, era=DEFAULT_ERA):
    conn = research.open_readonly(db_path)
    try:
        spells = load_charm_spells(conn)
        npcs = list(load_npcs(conn, era).values())
        chars, skipped = characters(spells, eq_dir, pqc_db, conn)
        source = dict(conn.execute("SELECT key, value FROM _meta").fetchall())
    finally:
        conn.close()
    baselines = score_npcs(npcs)
    max_cap = max((s["cap"] for s in spells), default=0)
    # Ship only NPCs some charm spell could take; the page narrows it to the chosen spell.
    rows = merge_copies(candidate(n) for n in npcs
                        if not n["immune"] and n["body"] not in JUNK_BODIES and n["level"] <= max_cap)
    rows.sort(key=lambda r: (-r["s"], r["n"], r["id"]))
    return {
        "npcs": rows, "spells": spells, "characters": chars, "skipped": skipped,
        "classes": {c: n for c, n in CHARM_CLASSES.items()}, "npc_classes": NPC_CLASSES, "bodies": BODY_NAMES,
        "target_labels": TARGET_LABELS, "target_bodies": {t: sorted(b) if b else None for t, b in TARGET_BODIES.items()},
        "baselines": {lvl: {k: round(v, 1) for k, v in b.items()} for lvl, b in baselines.items()},
        "terrain_rank": TERRAIN_RANK, "weights": WEIGHTS, "level_window": LEVEL_WINDOW, "db_version": source.get("source_version", "?"), "era": era,
    }


def csv_rows(data, spell_id=None):
    """Every shipped NPC, or only those the given charm spell can take."""
    spell = next((s for s in data["spells"] if s["id"] == spell_id), None)
    for r in data["npcs"]:
        if spell and not can_charm(spell, {"immune": "", "body": r["b"], "level": r["l"]}):
            continue
        yield {
            "npc_id": r["id"], "name": r["n"], "level": r["l"], "maxlevel": r["ml"],
            "class": NPC_CLASSES.get(r["c"], r["c"]), "body": BODY_NAMES.get(r["b"], r["b"]),
            "terrain": best_terrain(r["z"]), "score": r["s"],
            "dps": r["dps"], "dps_ratio": r["rd"], "hp": r["hp"], "hp_ratio": r["rh"], "ac": r["ac"],
            "ac_ratio": r["ra"], "hp_regen": r["rg"], "mr": r["mr"], "min_hit": r["mn"], "max_hit": r["mx"],
            "delay": r["dl"] / 10, "hits_per_round": r["h"], "abilities": "; ".join(r["t"]),
            "raid_target": int(r["raid"]), "zones": "; ".join(z[1] for z in r["z"]), "pqdi_url": PQDI_NPC.format(r["id"]),
        }


def write_csv(data, path, spell_id=None):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows(data, spell_id))


def render_html(data, nav=""):
    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return (template.replace("/*THEME*/", web.THEME_CSS).replace("/*DATA*/null", payload)
            .replace("<!--NAV-->", nav))


def page(params):
    try:
        era = float(params.get("era", DEFAULT_ERA))
    except ValueError:
        return web.error_page("Charm Targets", f"bad era: {params['era']!r}")
    try:
        data = gather(DEFAULT_DB, research.DEFAULT_EQ_DIR, research.DEFAULT_PQC_DB, era)
    except research.ResearchError as e:
        return web.error_page("Charm Targets", str(e))
    return render_html(data, nav=web.HOME_LINK)


def add_arguments(ap):
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--eq-dir", type=Path, default=research.DEFAULT_EQ_DIR, help="EverQuest dir with the Zeal exports")
    ap.add_argument("--pqc-db", type=Path, default=research.DEFAULT_PQC_DB, help="PQ Companion user.db (for levels)")
    ap.add_argument("--era", type=float, default=DEFAULT_ERA,
                    help="expansion used for the page's \"live spawns only\" filter (default 4 = Planes of Power)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)


def run(args):
    try:
        data = gather(args.db, args.eq_dir, args.pqc_db, args.era)
    except research.ResearchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    args.out.write_text(render_html(data), encoding="utf-8")
    write_csv(data, args.csv)
    spells = {s["id"]: s for s in data["spells"]}
    for ch in data["characters"]:
        if ch.get("new"):
            continue
        spell = spells.get(ch["spell"])
        if not spell:
            print(f"{ch['name']} ({CHARM_CLASSES[ch['class_id']]} {ch['level']}): no charm spell in the spellbook")
            continue
        low = max(1, spell["cap"] - LEVEL_WINDOW)
        top = [r for r in data["npcs"] if not r["raid"] and any(z[6] for z in r["z"]) and r["l"] >= low and
               can_charm(spell, {"immune": "", "body": r["b"], "level": r["l"]})]
        top.sort(key=lambda r: TERRAIN_RANK[best_terrain(r["z"], live_only=True)])  # stable: score order within
        print(f"{ch['name']} ({CHARM_CLASSES[ch['class_id']]} {ch['level']}), {spell['name']} "
              f"(up to level {spell['cap']}, {TARGET_LABELS[spell['targettype']]}): top from level {low} up, "
              f"outdoor first: " + ", ".join(f"{r['n']} L{r['l']} ({r['s']:.2f}, {best_terrain(r['z'], True)})"
                                             for r in top[:5]))
    if data["skipped"]:
        print("skipped (not a charm class): " + ", ".join(data["skipped"]))
    print(f"wrote {args.out}\nwrote {args.csv}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_arguments(ap)
    return run(ap.parse_args(argv))
