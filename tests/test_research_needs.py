"""Tests for scripts/research_needs.py: recipe loading and dedupe, owned-item merging and the needs view.

Run from the repo root: python3 -m unittest discover tests
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "research_needs.py"
sys.path.insert(0, str(SCRIPT.parent))

import research_needs as rn  # noqa: E402

ENC, NEC = 14, 11


def recipe(rid, cls, spell, components, scroll=None):
    return {"recipe_id": rid, "class_id": cls, "spell_id": spell, "scroll_id": scroll or 9000 + spell,
            "components": components}


class LoadRecipesTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE items (id INTEGER, Name TEXT, scrolleffect INTEGER DEFAULT 0);
            CREATE TABLE tradeskill_recipe (id INTEGER, tradeskill INTEGER, enabled INTEGER);
            CREATE TABLE tradeskill_recipe_entries (recipe_id INTEGER, item_id INTEGER, successcount INTEGER,
                componentcount INTEGER, iscontainer INTEGER);
        """)
        self.conn.executemany("INSERT INTO items VALUES (?, ?, ?)", [
            (1, "Tome of Endless Enchantments", 0), (2, "Phantasmal Tome", 0), (3, "Sorcerers Lexicon", 0),
            (4, "Sage's Box of Research", 0),
            (100, "Spell: Levitate", 261), (101, "Apprentice Rune (Azia)", 0),
            (10, "Part of Tasarin's Grimoire Pg. 23", 0), (11, "Part of Tasarin's Grimoire Pg. 23", 0),
            (12, "Rune of Expulsion", 0), (13, "Rune of Attraction", 0), (14, "A Quill", 0),
        ])
        recipes = [
            (770, 58, 1),  # ENC Levitate
            (771, 58, 1),  # WIZ Levitate
            (772, 58, 1),  # exact repeat of 771
            (773, 58, 0),  # disabled
            (774, 58, 1),  # not a spell scroll
            (775, 58, 1),  # wrong container
        ]
        self.conn.executemany("INSERT INTO tradeskill_recipe VALUES (?, ?, ?)", recipes)
        entries = []
        for rid, comps, tomes in [(770, [10, 11], [1, 2]), (771, [12, 13], [3]), (772, [13, 12], [3]),
                                  (773, [12, 13], [3]), (775, [12], [4])]:
            entries += [(rid, 100, 1, 0, 0)] + [(rid, c, 0, 1, 0) for c in comps] + [(rid, t, 0, 1, 1) for t in tomes]
        entries += [(774, 101, 1, 0, 0), (774, 14, 0, 1, 0), (774, 1, 0, 1, 1)]
        self.conn.executemany("INSERT INTO tradeskill_recipe_entries VALUES (?, ?, ?, ?, ?)", entries)

    def test_class_from_tome_and_dedupe(self):
        got = rn.load_recipes(self.conn)
        self.assertEqual([(r["recipe_id"], r["class_id"], r["spell_id"]) for r in got], [(770, 14, 261), (771, 12, 261)])
        self.assertEqual(got[0]["components"], {10: 1, 11: 1})

    def test_repeated_component_rows_are_summed(self):
        self.conn.execute("INSERT INTO tradeskill_recipe_entries VALUES (770, 10, 0, 1, 0)")
        self.assertEqual(rn.load_recipes(self.conn)[0]["components"], {10: 2, 11: 1})


class OwnedTest(unittest.TestCase):
    def test_shared_bank_taken_from_newest_export_only(self):
        owned = rn.collect_owned({
            "Old": (100, [("General1", 5, 1), ("SharedBank1-Slot1", 7, 1)]),
            "New": (200, [("Bank3-Slot2", 5, 2), ("SharedBank1-Slot1", 7, 1)]),
        })
        self.assertEqual(rn.owned_count(owned, 5), 3)
        self.assertEqual(rn.owned_count(owned, 7), 1)
        self.assertEqual(owned[7], [("Shared", "SharedBank1-Slot1", 1)])

    def test_parse_zeal_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp, "A-Spellbook.txt")
            book.write_text("Index\tSpellId\tLevel\tName\n0\t288\t1\tMinor Shielding\n1\t681\t4\tJuli`s Animation\n")
            inv = Path(tmp, "A-Inventory.txt")
            inv.write_text("Location\tName\tID\tCount\tSlots\nGeneral1\tEmpty\t0\t0\t0\n"
                           "Bank1-Slot2\tWords of Possession\t11815\t3\t0\nEar\tSonic Tooth Earring\t7166\t1\t0\n")
            self.assertEqual(rn.parse_spellbook(book), {288: 1, 681: 4})
            self.assertEqual(rn.parse_inventory(inv), [("Bank1-Slot2", 11815, 3), ("Ear", 7166, 1)])

    def test_infer_class(self):
        levels = {1: {ENC: 1, NEC: 4}, 2: {ENC: 8, NEC: 8}, 3: {NEC: 12}}
        self.assertEqual(rn.infer_class({1: 4, 2: 8, 3: 12}, levels), NEC)
        self.assertIsNone(rn.infer_class({2: 8}, levels))


class BuildViewTest(unittest.TestCase):
    SPELLS = {1: {"name": "Alpha", "levels": {ENC: 10}}, 2: {"name": "Beta", "levels": {ENC: 20}},
              3: {"name": "Gamma", "levels": {ENC: 30}}, 4: {"name": "Delta", "levels": {ENC: 5}}}

    def view(self, recipes, owned, book=None, scrolls=None):
        return rn.build_view(ENC, book or {}, recipes, self.SPELLS, scrolls or {}, owned)

    def test_shared_word_counted_once_per_spell(self):
        v = self.view([recipe(1, ENC, 1, {50: 1, 51: 1}), recipe(2, ENC, 2, {50: 1, 52: 1}),
                       recipe(3, NEC, 3, {50: 1})], {50: [("A", "Bank1", 1)]})
        w = {r["item_id"]: r for r in v["words"]}
        self.assertEqual((w[50]["needed"], w[50]["owned"], w[50]["still_need"]), (2, 1, 1))
        self.assertEqual(w[50]["duplicate"], "used by 2 spells")
        self.assertEqual((w[50]["min_level"], w[50]["max_level"]), (10, 20))
        self.assertEqual(len(v["spells"]), 2)

    def test_known_spell_and_owned_scroll_excluded(self):
        v = self.view([recipe(1, ENC, 1, {50: 1}), recipe(2, ENC, 2, {51: 1})], {9002: [("A", "General1", 1)]},
                      book={1: 10}, scrolls={2: {9002}})
        self.assertEqual([(s["name"], s["status"]) for s in v["spells"]], [("Beta", "scroll owned")])
        self.assertEqual(v["words"], [])

    def test_alternative_with_fewest_missing_is_counted(self):
        owned = {60: [("A", "Bank1", 1)], 61: [("A", "Bank1", 1)]}
        v = self.view([recipe(1, ENC, 4, {50: 1, 51: 1}), recipe(2, ENC, 4, {60: 1, 61: 1})], owned)
        (spell,) = v["spells"]
        self.assertEqual((spell["recipe_id"], spell["status"]), (2, "ready"))
        self.assertEqual([a["recipe_id"] for a in spell["alternatives"]], [1])
        self.assertEqual({w["item_id"] for w in v["words"]}, {60, 61})

    def test_ready_with_contended_words(self):
        v = self.view([recipe(1, ENC, 1, {50: 1}), recipe(2, ENC, 2, {50: 1}), recipe(3, ENC, 3, {51: 1})],
                      {50: [("A", "Bank1", 1)], 51: [("A", "Bank1", 1)]})
        self.assertEqual({s["name"]: s["status"] for s in v["spells"]},
                         {"Alpha": "ready (words shared)", "Beta": "ready (words shared)", "Gamma": "ready"})

    def test_buyable_spell_words_not_counted(self):
        sold = {1: [{"zone": "pok", "zone_name": "PoK", "npc": "A", "price": 1}]}
        v = rn.build_view(ENC, {}, [recipe(1, ENC, 1, {50: 1}), recipe(2, ENC, 2, {50: 1})], self.SPELLS, {},
                          {50: [("A", "Bank1", 1)]}, sold)
        self.assertEqual({s["name"]: s["status"] for s in v["spells"]}, {"Alpha": "buy", "Beta": "ready"})
        (w,) = v["words"]
        self.assertEqual((w["needed"], w["still_need"]), (1, 0))
        self.assertEqual([(s["spell_id"], s["buyable"]) for s in w["spells"]], [(1, True), (2, False)])
        self.assertEqual(v["shopping"], [{"zone": "pok", "zone_name": "PoK", "spells": [1]}])

    def test_spare_copies_flagged(self):
        v = self.view([recipe(1, ENC, 1, {50: 1})], {50: [("A", "Bank1", 3)]})
        self.assertEqual(v["words"][0]["duplicate"], "2 spare")


class VendorTest(unittest.TestCase):
    def test_live_in_era(self):
        flags = {"OldPlane_Fear"}
        self.assertTrue(rn.live_in_era((-1, -1, None, None), 4.0, flags))
        self.assertTrue(rn.live_in_era((4.0, 5.0, "", ""), 4.0, flags))
        self.assertFalse(rn.live_in_era((4.0, 5.0, None, None), 3.0, flags))
        self.assertFalse(rn.live_in_era((0.0, 2.9, None, None), 3.0, flags))
        self.assertTrue(rn.live_in_era((0.0, 2.0, None, None), 2.0, flags))
        self.assertTrue(rn.live_in_era((-1, -1, "OldPlane_Fear", None), 4.0, flags))
        self.assertFalse(rn.live_in_era((-1, -1, "GoingPostal", None), 4.0, flags))
        self.assertFalse(rn.live_in_era((-1, -1, None, "OldPlane_Fear"), 4.0, flags))

    def test_load_vendors_era_and_spawn(self):
        conn = sqlite3.connect(":memory:")
        era_cols = "min_expansion REAL DEFAULT -1, max_expansion REAL DEFAULT -1, content_flags TEXT, content_flags_disabled TEXT"
        conn.executescript(f"""
            CREATE TABLE content_flags (flag_name TEXT, enabled INTEGER);
            CREATE TABLE items (id INTEGER, Name TEXT, price INTEGER);
            CREATE TABLE merchantlist (merchantid INTEGER, item INTEGER, {era_cols});
            CREATE TABLE npc_types (id INTEGER, name TEXT, merchant_id INTEGER);
            CREATE TABLE spawnentry (spawngroupID INTEGER, npcID INTEGER, {era_cols});
            CREATE TABLE spawn2 (spawngroupID INTEGER, zone TEXT, {era_cols});
            CREATE TABLE zone (short_name TEXT, long_name TEXT, expansion INTEGER);
            INSERT INTO items VALUES (1, 'Spell: Levitate', 1300);
            INSERT INTO zone VALUES ('poknowledge', 'Plane of Knowledge', 4), ('cabeast', 'Cabilis East', 2);
            INSERT INTO npc_types VALUES (10, 'Mystic_Ryrin', 100), (11, '#Vessel_Grott', 101), (12, 'Unspawned', 102);
            INSERT INTO merchantlist (merchantid, item) VALUES (100, 1), (101, 1), (102, 1);
            INSERT INTO spawnentry (spawngroupID, npcID) VALUES (1, 10), (2, 11), (2, 11);
            INSERT INTO spawn2 (spawngroupID, zone) VALUES (1, 'poknowledge'), (2, 'cabeast');
        """)
        pop = rn.load_vendors(conn, [1], 4.0)[1]
        self.assertEqual([(v["zone_name"], v["npc"]) for v in pop],
                         [("Cabilis East", "Vessel Grott"), ("Plane of Knowledge", "Mystic Ryrin")])
        self.assertEqual([v["zone"] for v in rn.load_vendors(conn, [1], 3.0)[1]], ["cabeast"])

    def test_spell_vendors_cheapest_scroll(self):
        vendors = {1: [{"zone": "pok", "zone_name": "PoK", "npc": "A", "price": 50}],
                   2: [{"zone": "pok", "zone_name": "PoK", "npc": "A", "price": 30}]}
        self.assertEqual(rn.spell_vendors({7: {1, 2}, 8: {3}}, vendors),
                         {7: [{"zone": "pok", "zone_name": "PoK", "npc": "A", "price": 30}]})

    def test_shopping_route_greedy(self):
        def v(zone):
            return {"zone": zone, "zone_name": zone.upper(), "npc": "x", "price": 1}
        rows = [{"spell_id": 1, "status": "buy", "vendors": [v("a"), v("pok")]},
                {"spell_id": 2, "status": "buy", "vendors": [v("pok")]},
                {"spell_id": 3, "status": "buy", "vendors": [v("b")]},
                {"spell_id": 4, "status": "missing words", "vendors": []}]
        self.assertEqual([(r["zone"], r["spells"]) for r in rn.shopping_route(rows)], [("pok", [1, 2]), ("b", [3])])


class ComponentTypeTest(unittest.TestCase):
    def test_types(self):
        for name, kind in [("Words of Possession", "Word"), ("Rune of Xegony", "Rune"),
                           ("Part of Tasarin's Grimoire Pg. 23", "Page"), ("Velishouls Tome Pg.76", "Page"),
                           ("Spell: Elemental: Air", "Scroll"), ("Bloodstone", "Other")]:
            self.assertEqual(rn.component_type(name), kind, name)


class TwinTest(unittest.TestCase):
    def test_same_name_items(self):
        items = {10: "Pg. 23", 11: "Pg. 23", 12: "Rune"}
        twins = rn.name_twins(items)
        self.assertEqual(twins, {10: [11], 11: [10], 12: []})
        data_items = {i: {"name": n, "twins": twins[i]} for i, n in items.items()}
        self.assertEqual(rn.twin_note(10, data_items, {10, 11}), "same name as #11: a different item")
        self.assertEqual(rn.twin_note(10, data_items, {10}), "same name as #11 (not needed): a different item")
        self.assertEqual(rn.twin_note(12, data_items, {12}), "")


if __name__ == "__main__":
    unittest.main()
