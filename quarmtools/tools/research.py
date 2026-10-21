"""List the research words/pages a character still needs, as a sortable HTML page and a CSV.

Usage: ./quarm research [--db FILE] [--eq-dir DIR] [--pqc-db FILE] [--era N] [--out FILE] [--csv FILE]

Reads the research recipes from data/quarm.sqlite and each character's Zeal exports from the
EverQuest directory (<Name>-Spellbook.txt and <Name>-Inventory.txt; write them in game with
/outputfile spellbook and /outputfile inventory). The front end (./quarm) serves the page at
/research, rebuilt on every load. From the command line it writes one self-contained HTML file with a
character picker (every character with a spellbook export, plus "new character" entries for each
research class) and a CSV with the same rows for every character.

How it decides what is needed:

- A research recipe is an enabled Research (tradeskill 58) recipe whose output is a "Spell:"
  scroll and whose container is one class's research tome (CLASS_TOMES). The tome decides the
  class: several spells have a recipe for more than one class with different words.
- Recipes with the same class, spell and components are collapsed (the dump repeats some). A
  component listed twice in a recipe is needed twice.
- Some pages are two different items with the same name (each half of a page); the output
  shows item ids for those so they can be told apart.
- A spell is missing if its id is not in the spellbook. If the finished scroll is in any inventory,
  the spell is marked "scroll owned" and its words are not counted.
- If a merchant sells the finished scroll, the spell is marked "buy" and its words are not counted
  either (the page can count them anyway). A merchant counts only if it spawns somewhere, and only
  merchant list entries, spawns and zones that are live in --era (default 4 = Planes of Power, so
  Plane of Knowledge vendors count) are used, along with the dump's content flags. The page also
  suggests zones to shop in: repeatedly the zone that sells the most still-unbought spells.
- "ready" means every word is owned; it becomes "ready (words shared)" when other missing spells
  need the same copies.
- If a class has two different recipes for one spell, the one with the fewest words still to get
  is counted; the other is shown as an alternative only.
- Owned counts cover every character's bags, bank and shared bank. Every Inventory.txt repeats the
  shared bank, so only the newest export's SharedBank rows are used.

Character class is inferred from the spellbook (the class whose level matches each spell's
listed level). Level comes from PQ Companion's user.db when it is readable, else the highest
spell level in the book; the page lets you change it.
"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

from quarmtools import common, web

NAME = "research"
SUMMARY = "Spell research words and pages your characters still need, with merchants and a shopping route."

DEFAULT_DB = common.DEFAULT_DB
DEFAULT_OUT = common.DATA_DIR / "research_needs.html"
DEFAULT_CSV = common.DATA_DIR / "research_needs.csv"
TEMPLATE = Path(__file__).resolve().with_suffix(".html")
WINE_USER = Path.home() / "Games" / "EQQuarm" / "drive_c" / "users" / "steamuser"
DEFAULT_EQ_DIR = Path(os.environ.get("QUARM_EQ_DIR", Path.home() / "Games" / "EQQuarm" / "drive_c" / "Quarm"))
DEFAULT_PQC_DB = WINE_USER / ".pq-companion" / "user.db"
# Quarm's current expansion. The dump's World:CurrentExpansion rule still says 3.0 (Luclin), but
# Planes of Power (4) is live; pass --era to override.
DEFAULT_ERA = 4.0

RESEARCH_TRADESKILL = 58
# EQ class ids (spells_new.classesN uses the same numbering).
CLASS_NAMES = {1: "Warrior", 2: "Cleric", 3: "Paladin", 4: "Ranger", 5: "Shadow Knight", 6: "Druid",
               7: "Monk", 8: "Bard", 9: "Rogue", 10: "Shaman", 11: "Necromancer", 12: "Wizard",
               13: "Magician", 14: "Enchanter", 15: "Beastlord"}
CLASS_TOMES = {
    "Book of Dark Bindings": 11, "Warlocks Book of Binding": 11,
    "Sorcerers Lexicon": 12, "Lexicon": 12,
    "Elemental Grimoire": 13, "Arch Magus Grimoire": 13,
    "Tome of Endless Enchantments": 14, "Phantasmal Tome": 14,
}
PQDI_ITEM = "https://www.pqdi.cc/item/{}"
PQDI_SPELL = "https://www.pqdi.cc/spell/{}"

CSV_FIELDS = ["character", "class", "word", "item_id", "type", "needed", "owned", "still_need",
              "spell_count", "spells", "min_level", "max_level", "duplicate", "owned_where", "sold_by",
              "pqdi_url"]


class ResearchError(Exception):
    pass


def open_readonly(path):
    try:
        return common.open_readonly(path)
    except FileNotFoundError as e:
        raise ResearchError(str(e)) from None


def component_type(name):
    if name.startswith("Words of"):
        return "Word"
    if name.startswith("Rune of"):
        return "Rune"
    if re.search(r"\bPg\.", name):
        return "Page"
    if name.startswith("Spell:"):
        return "Scroll"
    return "Other"


# ---------------------------------------------------------------------------- database


def load_recipes(conn):
    """Research recipes as dicts {recipe_id, class_id, spell_id, scroll_id, components}.

    components maps item id -> count. Recipes that repeat another one's class, spell and
    components are dropped (the lowest recipe id is kept).
    """
    tomes = ",".join("?" * len(CLASS_TOMES))
    rows = conn.execute(f"""
        SELECT r.id, o.id, o.scrolleffect, c.Name
        FROM tradeskill_recipe r
        JOIN tradeskill_recipe_entries eo ON eo.recipe_id = r.id AND eo.successcount > 0
        JOIN items o ON o.id = eo.item_id AND o.scrolleffect > 0 AND o.Name LIKE 'Spell:%'
        JOIN tradeskill_recipe_entries ec ON ec.recipe_id = r.id AND ec.iscontainer = 1
        JOIN items c ON c.id = ec.item_id AND c.Name IN ({tomes})
        WHERE r.tradeskill = ? AND r.enabled = 1
        ORDER BY r.id""", (*CLASS_TOMES, RESEARCH_TRADESKILL)).fetchall()
    found = {}
    for recipe_id, scroll_id, spell_id, tome in rows:
        found.setdefault(recipe_id, (CLASS_TOMES[tome], spell_id, scroll_id))
    components = defaultdict(Counter)
    if found:
        marks = ",".join("?" * len(found))
        for recipe_id, item_id, count in conn.execute(f"""
                SELECT recipe_id, item_id, componentcount FROM tradeskill_recipe_entries
                WHERE recipe_id IN ({marks}) AND iscontainer = 0 AND componentcount > 0""", list(found)):
            components[recipe_id][item_id] += count
    recipes = [{"recipe_id": rid, "class_id": cls, "spell_id": spell, "scroll_id": scroll,
                "components": dict(components[rid])} for rid, (cls, spell, scroll) in sorted(found.items())]
    return dedupe_recipes(recipes)


def dedupe_recipes(recipes):
    seen = set()
    out = []
    for r in sorted(recipes, key=lambda r: r["recipe_id"]):
        key = (r["class_id"], r["spell_id"], frozenset(r["components"].items()))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def load_spells(conn, spell_ids):
    """spell id -> {name, levels: {class_id: level}} (levels only for classes that can use it)."""
    spell_ids = sorted(set(spell_ids))
    if not spell_ids:
        return {}
    cols = ", ".join(f"classes{c}" for c in CLASS_NAMES)
    marks = ",".join("?" * len(spell_ids))
    out = {}
    for row in conn.execute(f"SELECT id, name, {cols} FROM spells_new WHERE id IN ({marks})", spell_ids):
        levels = {c: lvl for c, lvl in zip(CLASS_NAMES, row[2:]) if lvl is not None and lvl < 255}
        out[row[0]] = {"name": row[1], "levels": levels}
    return out


def load_class_levels(conn, spell_ids):
    """spell id -> {class_id: level} for any spells (used to infer a character's class)."""
    return {sid: s["levels"] for sid, s in load_spells(conn, spell_ids).items()}


def load_item_names(conn, item_ids):
    item_ids = sorted(set(item_ids))
    if not item_ids:
        return {}
    marks = ",".join("?" * len(item_ids))
    return {i: n.rstrip("\0") for i, n in conn.execute(f"SELECT id, Name FROM items WHERE id IN ({marks})", item_ids)}


def load_scroll_items(conn, spell_ids):
    """spell id -> set of scroll item ids that teach it (there can be several)."""
    spell_ids = sorted(set(spell_ids))
    out = defaultdict(set)
    if spell_ids:
        marks = ",".join("?" * len(spell_ids))
        for item_id, spell_id in conn.execute(
                f"SELECT id, scrolleffect FROM items WHERE Name LIKE 'Spell:%' AND scrolleffect IN ({marks})", spell_ids):
            out[spell_id].add(item_id)
    return out


def enabled_content_flags(conn):
    try:
        return {n for n, on in conn.execute("SELECT flag_name, enabled FROM content_flags") if on}
    except sqlite3.Error:
        return set()


def live_in_era(row, era, flags):
    """Whether a row with (min_expansion, max_expansion, content_flags, content_flags_disabled) is live."""
    lo, hi, need, disabled = row
    if lo is not None and lo != -1 and era < lo:
        return False
    if hi is not None and hi != -1 and era > hi:
        return False
    if need and not all(f.strip() in flags for f in need.split(",") if f.strip()):
        return False
    if disabled and any(f.strip() in flags for f in disabled.split(",") if f.strip()):
        return False
    return True


def npc_display_name(name):
    return name.lstrip("#").replace("_", " ").strip()


def load_vendors(conn, item_ids, era):
    """item id -> [{zone, zone_name, npc, price}] for merchants that spawn and are live in the era.

    One entry per (item, zone, merchant). price is the item's base price in copper; what a merchant
    actually charges depends on charisma and faction.
    """
    item_ids = sorted(set(item_ids))
    out = defaultdict(list)
    if not item_ids:
        return out
    flags = enabled_content_flags(conn)
    marks = ",".join("?" * len(item_ids))
    seen = set()
    for row in conn.execute(f"""
            SELECT ml.item, s2.zone, COALESCE(z.long_name, s2.zone), n.id, n.name, i.price, z.expansion,
                   ml.min_expansion, ml.max_expansion, ml.content_flags, ml.content_flags_disabled,
                   se.min_expansion, se.max_expansion, se.content_flags, se.content_flags_disabled,
                   s2.min_expansion, s2.max_expansion, s2.content_flags, s2.content_flags_disabled
            FROM merchantlist ml
            JOIN npc_types n ON n.merchant_id = ml.merchantid AND n.merchant_id > 0
            JOIN spawnentry se ON se.npcID = n.id
            JOIN spawn2 s2 ON s2.spawngroupID = se.spawngroupID
            JOIN items i ON i.id = ml.item
            LEFT JOIN zone z ON z.short_name = s2.zone
            WHERE ml.item IN ({marks}) AND s2.zone IS NOT NULL AND s2.zone != ''
            ORDER BY ml.item, z.long_name, n.name""", item_ids):
        item_id, zone, zone_name, npc_id, npc, price, zone_era = row[:7]
        if zone_era is not None and zone_era > era:
            continue
        if not all(live_in_era(row[k:k + 4], era, flags) for k in (7, 11, 15)):
            continue
        if (item_id, zone, npc_id) in seen:
            continue
        seen.add((item_id, zone, npc_id))
        out[item_id].append({"zone": zone, "zone_name": zone_name, "npc": npc_display_name(npc), "price": price or 0})
    return out


def spell_vendors(scrolls, vendors):
    """spell id -> vendors selling any scroll for it, cheapest scroll per (zone, merchant)."""
    out = {}
    for spell_id, scroll_ids in scrolls.items():
        best = {}
        for sid in scroll_ids:
            for v in vendors.get(sid, []):
                key = (v["zone"], v["npc"])
                if key not in best or v["price"] < best[key]["price"]:
                    best[key] = v
        if best:
            out[spell_id] = sorted(best.values(), key=lambda v: (v["zone_name"], v["npc"]))
    return out


def shopping_route(spell_rows):
    """Zones to visit for the "buy" spells: repeatedly take the zone selling the most remaining ones."""
    remaining = {r["spell_id"]: {v["zone"] for v in r["vendors"]} for r in spell_rows if r["status"] == "buy"}
    names = {v["zone"]: v["zone_name"] for r in spell_rows for v in r["vendors"]}
    route = []
    while remaining:
        counts = Counter(z for zones in remaining.values() for z in zones)
        zone = min(counts, key=lambda z: (-counts[z], names[z]))
        covered = sorted(s for s, zones in remaining.items() if zone in zones)
        route.append({"zone": zone, "zone_name": names[zone], "spells": covered})
        for s in covered:
            del remaining[s]
    return route


# ---------------------------------------------------------------------------- Zeal exports


def read_tsv(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, r)) for r in rows[1:] if r]


def parse_spellbook(path):
    """{spell_id: level} from a Zeal <Name>-Spellbook.txt."""
    book = {}
    for row in read_tsv(path):
        try:
            book[int(row["SpellId"])] = int(row["Level"])
        except (KeyError, ValueError):
            continue
    return book


def parse_inventory(path):
    """[(location, item_id, count)] from a Zeal <Name>-Inventory.txt, skipping empty slots."""
    out = []
    for row in read_tsv(path):
        try:
            item_id, count = int(row["ID"]), int(row["Count"] or 1)
        except (KeyError, ValueError):
            continue
        if item_id > 0:
            out.append((row.get("Location", ""), item_id, max(count, 1)))
    return out


def collect_owned(inventories):
    """Merge inventories {character: (mtime, [(location, item_id, count)])}.

    Returns item id -> [(character, location, count)]. Shared bank rows are taken only from the
    newest export, since every character's export repeats them.
    """
    newest = max(inventories, key=lambda c: inventories[c][0], default=None)
    owned = defaultdict(list)
    for char, (_, rows) in inventories.items():
        for location, item_id, count in rows:
            if location.startswith("SharedBank") and char != newest:
                continue
            owned[item_id].append(("Shared" if location.startswith("SharedBank") else char, location, count))
    return owned


def infer_class(book, class_levels):
    """Class id whose spell levels best match the spellbook's, or None."""
    score = Counter()
    for spell_id, level in book.items():
        for cls, lvl in class_levels.get(spell_id, {}).items():
            if lvl == level:
                score[cls] += 1
    if not score:
        return None
    best = score.most_common()
    return best[0][0] if len(best) == 1 or best[0][1] > best[1][1] else None


def pqc_levels(path):
    """{lower-case name: level} from PQ Companion's user.db, or {} if it can't be read."""
    try:
        conn = open_readonly(path)
        try:
            return {n.lower(): lvl for n, lvl in conn.execute("SELECT name, level FROM characters")}
        finally:
            conn.close()
    except (ResearchError, sqlite3.Error):
        return {}


# ---------------------------------------------------------------------------- needs


def name_twins(items):
    """item id -> ids of the other items with the same name (e.g. the two halves of a page)."""
    by_name = defaultdict(list)
    for i, n in items.items():
        by_name[n].append(i)
    return {i: sorted(j for j in by_name[n] if j != i) for i, n in items.items()}


def twin_note(item_id, items, needed_ids):
    twins = items.get(item_id, {}).get("twins", [])
    if not twins:
        return ""
    ids = ", ".join(f"#{t}" + ("" if t in needed_ids else " (not needed)") for t in twins)
    return f"same name as {ids}: a different item"


def owned_count(owned, item_id):
    return sum(c for _, _, c in owned.get(item_id, []))


def build_view(class_id, book, recipes, spells, scrolls, owned, vendors=None):
    """Spell rows and word rows for one character (book = {} for a new character).

    vendors maps spell id -> merchants selling its scroll (see spell_vendors). Words for spells
    that can be bought are listed with buyable=True but left out of the needed counts.
    """
    vendors = vendors or {}
    by_spell = defaultdict(list)
    for r in recipes:
        if r["class_id"] == class_id and r["spell_id"] not in book:
            by_spell[r["spell_id"]].append(r)

    def still_needed(r):
        return sum(max(0, n - owned_count(owned, i)) for i, n in r["components"].items())

    spell_rows, needs = [], defaultdict(Counter)
    for spell_id, options in by_spell.items():
        options.sort(key=lambda r: (still_needed(r), r["recipe_id"]))
        chosen, alternatives = options[0], options[1:]
        spell = spells.get(spell_id, {"name": f"spell {spell_id}", "levels": {}})
        level = spell["levels"].get(class_id)
        scroll_owned = any(owned_count(owned, s) for s in scrolls.get(spell_id, ()))
        comps = [{"item_id": i, "needed": n, "owned": owned_count(owned, i)} for i, n in sorted(chosen["components"].items())]
        sold = vendors.get(spell_id, [])
        if scroll_owned:
            status = "scroll owned"
        elif sold:
            status = "buy"
        elif all(c["owned"] >= c["needed"] for c in comps):
            status = "ready"
        else:
            status = "missing words"
        spell_rows.append({
            "spell_id": spell_id, "name": spell["name"], "level": level, "status": status,
            "recipe_id": chosen["recipe_id"], "components": comps, "vendors": sold,
            "alternatives": [{"recipe_id": a["recipe_id"], "components": [{"item_id": i, "needed": n}
                             for i, n in sorted(a["components"].items())]} for a in alternatives],
        })
        if not scroll_owned:
            for i, n in chosen["components"].items():
                needs[i][spell_id] += n
    buyable = {r["spell_id"] for r in spell_rows if r["status"] == "buy"}

    def counted(per_spell):
        return {s: n for s, n in per_spell.items() if s not in buyable}

    # A spell is only truly ready if its words aren't also claimed by other missing spells.
    for row in spell_rows:
        if row["status"] == "ready" and any(sum(counted(needs[c["item_id"]]).values()) > c["owned"] for c in row["components"]):
            row["status"] = "ready (words shared)"

    word_rows = []
    for item_id, all_spells in needs.items():
        per_spell = counted(all_spells)
        needed = sum(per_spell.values())
        have = owned_count(owned, item_id)
        levels = [spells.get(s, {}).get("levels", {}).get(class_id) for s in per_spell]
        levels = [lvl for lvl in levels if lvl is not None]
        dup = []
        if len(per_spell) > 1:
            dup.append(f"used by {len(per_spell)} spells")
        if any(n > 1 for n in per_spell.values()):
            dup.append("multiple per recipe")
        if have > needed:
            dup.append(f"{have - needed} spare")
        word_rows.append({
            "item_id": item_id, "needed": needed, "owned": have, "still_need": max(0, needed - have),
            "spells": [{"spell_id": s, "count": n, "buyable": s in buyable} for s, n in sorted(all_spells.items())],
            "min_level": min(levels) if levels else None, "max_level": max(levels) if levels else None,
            "duplicate": "; ".join(dup),
        })
    spell_rows.sort(key=lambda r: (r["level"] or 0, r["name"]))
    word_rows.sort(key=lambda r: (r["min_level"] or 0, r["item_id"]))
    return {"spells": spell_rows, "words": word_rows, "shopping": shopping_route(spell_rows)}


def gather(db_path, eq_dir, pqc_db, era=DEFAULT_ERA):
    conn = open_readonly(db_path)
    try:
        recipes = load_recipes(conn)
        research_classes = sorted({r["class_id"] for r in recipes})
        spells = load_spells(conn, [r["spell_id"] for r in recipes])
        scrolls = load_scroll_items(conn, spells)
        component_ids = {i for r in recipes for i in r["components"]}
        vendors = load_vendors(conn, component_ids | {i for v in scrolls.values() for i in v}, era)
        sold = spell_vendors(scrolls, vendors)

        books, inventories = {}, {}
        for path in sorted(eq_dir.glob("*-Spellbook.txt")):
            books[path.name[:-len("-Spellbook.txt")]] = parse_spellbook(path)
        for path in sorted(eq_dir.glob("*-Inventory.txt")):
            inventories[path.name[:-len("-Inventory.txt")]] = (path.stat().st_mtime, parse_inventory(path))
        owned = collect_owned(inventories)
        class_levels = load_class_levels(conn, {s for b in books.values() for s in b})
        levels = pqc_levels(pqc_db)

        characters, skipped = [], []
        for name, book in books.items():
            cls = infer_class(book, class_levels)
            if cls not in research_classes:
                skipped.append(f"{name} ({CLASS_NAMES.get(cls, 'unknown class')})")
                continue
            level = levels.get(name.lower()) or max(book.values(), default=1)
            characters.append({"key": name, "name": name, "class_id": cls, "level": level,
                               **build_view(cls, book, recipes, spells, scrolls, owned, sold)})
        for cls in research_classes:
            characters.append({"key": f"new-{cls}", "name": f"New {CLASS_NAMES[cls]}", "class_id": cls,
                               "level": 65 if era >= 4 else 60, "new": True,
                               **build_view(cls, {}, recipes, spells, scrolls, owned, sold)})

        items = load_item_names(conn, component_ids)
        source = dict(conn.execute("SELECT key, value FROM _meta").fetchall())
    finally:
        conn.close()

    twins = name_twins(items)
    owned_where = {i: [f"{who}: {loc}" + (f" x{n}" if n > 1 else "") for who, loc, n in owned[i]]
                   for i in items if i in owned}
    return {
        "characters": characters, "skipped": skipped,
        "classes": {c: CLASS_NAMES[c] for c in research_classes},
        "items": {i: {"name": n, "type": component_type(n), "twins": twins[i], "vendors": vendors.get(i, [])}
                  for i, n in items.items()},
        "spell_names": {s: v["name"] for s, v in spells.items()},
        "owned_where": owned_where,
        "exports": {name: mtime for name, (mtime, _) in inventories.items()},
        "db_version": source.get("source_version", "?"), "era": era,
    }


# ---------------------------------------------------------------------------- output


def csv_rows(data):
    items, names = data["items"], data["spell_names"]
    for ch in data["characters"]:
        needed_ids = {w["item_id"] for w in ch["words"] if w["needed"]}
        for w in ch["words"]:
            if not w["needed"]:
                continue
            item = items.get(w["item_id"], {"name": f"item {w['item_id']}", "type": "Other"})
            duplicate = "; ".join(filter(None, [w["duplicate"], twin_note(w["item_id"], items, needed_ids)]))
            yield {
                "character": ch["name"], "class": data["classes"][ch["class_id"]],
                "word": item["name"], "item_id": w["item_id"], "type": item["type"],
                "needed": w["needed"], "owned": w["owned"], "still_need": w["still_need"],
                "spell_count": sum(not s["buyable"] for s in w["spells"]),
                "spells": "; ".join(names.get(s["spell_id"], str(s["spell_id"])) + (f" x{s['count']}" if s["count"] > 1 else "")
                                    for s in w["spells"] if not s["buyable"]),
                "min_level": w["min_level"], "max_level": w["max_level"], "duplicate": duplicate,
                "owned_where": "; ".join(data["owned_where"].get(w["item_id"], [])),
                "sold_by": "; ".join(f"{v['npc']} ({v['zone_name']})" for v in item.get("vendors", [])),
                "pqdi_url": PQDI_ITEM.format(w["item_id"]),
            }


def write_csv(data, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows(data))


def render_html(data, nav=""):
    """The page for data; nav is HTML put before the title (the front end's home link)."""
    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("/*DATA*/null", payload).replace("<!--NAV-->", nav)


def write_html(data, path):
    path.write_text(render_html(data), encoding="utf-8")


def page(params):
    """Front-end page: rebuilt from the database and the current Zeal exports on every load (?era=N)."""
    try:
        era = float(params.get("era", DEFAULT_ERA))
    except ValueError:
        return web.error_page("Research Needs", f"bad era: {params['era']!r}")
    try:
        if not DEFAULT_EQ_DIR.is_dir():
            raise ResearchError(f"EverQuest directory not found: {DEFAULT_EQ_DIR} (set QUARM_EQ_DIR)")
        data = gather(DEFAULT_DB, DEFAULT_EQ_DIR, DEFAULT_PQC_DB, era)
    except ResearchError as e:
        return web.error_page("Research Needs", str(e))
    return render_html(data, nav=web.HOME_LINK)


def add_arguments(ap):
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--eq-dir", type=Path, default=DEFAULT_EQ_DIR, help="EverQuest dir with the Zeal exports")
    ap.add_argument("--pqc-db", type=Path, default=DEFAULT_PQC_DB, help="PQ Companion user.db (for levels)")
    ap.add_argument("--era", type=float, default=DEFAULT_ERA,
                    help="current expansion for vendor availability (default 4 = Planes of Power)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)


def run(args):
    try:
        if not args.eq_dir.is_dir():
            raise ResearchError(f"EverQuest directory not found: {args.eq_dir}")
        data = gather(args.db, args.eq_dir, args.pqc_db, args.era)
    except ResearchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    write_html(data, args.out)
    write_csv(data, args.csv)
    for ch in data["characters"]:
        if not ch.get("new"):
            left = sum(w["still_need"] for w in ch["words"])
            buy = sum(s["status"] == "buy" for s in ch["spells"])
            print(f"{ch['name']} ({data['classes'][ch['class_id']]} {ch['level']}): "
                  f"{len(ch['spells'])} missing research spells ({buy} sold by merchants), "
                  f"{left} words/pages still to get for the rest")
    if data["skipped"]:
        print("skipped (no research spells): " + ", ".join(data["skipped"]))
    print(f"wrote {args.out}\nwrote {args.csv}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    add_arguments(ap)
    return run(ap.parse_args(argv))
