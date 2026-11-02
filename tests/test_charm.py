"""Tests for quarmtools/tools/charm.py: special abilities, the DPS model, charm rules, spell choice and scoring.

Run from the repo root: python3 -m unittest discover tests
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quarmtools import common  # noqa: E402
from quarmtools.tools import charm  # noqa: E402

ENC, NEC, DRU = 14, 11, 6


def npc(**kw):
    base = {"id": 1, "name": "a mob", "raw_name": "a_mob", "level": 30, "maxlevel": 30, "class_id": 1, "body": 1,
            "hp": 1000, "mindmg": 10, "maxdmg": 50, "attack_count": -1, "attack_delay": 30, "ac": 100, "mr": 20,
            "hp_regen": 10, "abilities": {}, "caster": False, "raid": False, "rare": False, "see_invis": False,
            "runspeed": 1, "spawns": [{"zone": "x", "zone_name": "X", "expansion": 0, "points": 1, "chance": 100,
                                       "respawn": 600, "live": True}]}
    base.update(kw)
    return base


def spell(sid, name, ttype, cap, levels, scroll=True, resist_diff=0, duration=205):
    return {"id": sid, "name": name, "targettype": ttype, "cap": cap, "levels": levels, "scroll": scroll,
            "resist_diff": resist_diff, "duration": duration}


SPELLS = [
    spell(300, "Charm", 5, 25, {ENC: 12}),
    spell(1705, "Boltran`s Agacerie", 5, 53, {ENC: 53}, resist_diff=-10),
    spell(1707, "Dictate", 5, 58, {ENC: 60}, resist_diff=-1000, duration=8),
    spell(196, "Dominate Undead", 10, 32, {NEC: 20}),
    spell(2759, "Undead Pact", 10, 46, {NEC: 1}, scroll=False, resist_diff=-1000, duration=0),
    spell(260, "Charm Animals", 9, 33, {DRU: 24}),
]


class SpecialAbilitiesTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(charm.parse_special_abilities("10,1^14,1,0^5,0"), {10: [1], 14: [1, 0]})
        self.assertEqual(charm.parse_special_abilities(""), {})
        self.assertEqual(charm.parse_special_abilities(None), {})
        self.assertEqual(charm.parse_special_abilities("3,1,30,75,50^junk^7"), {3: [1, 30, 75, 50]})


class DpsTest(unittest.TestCase):
    def test_double_attack_by_level(self):
        self.assertEqual(charm.double_attack_chance(5), 0)
        self.assertAlmostEqual(charm.double_attack_chance(20), 100 / 500)
        self.assertAlmostEqual(charm.double_attack_chance(44), (210 + 44) / 500)
        self.assertAlmostEqual(charm.double_attack_chance(55), (250 + 55) / 500)

    def test_hits_per_round(self):
        self.assertAlmostEqual(charm.hits_per_round(5, 1, -1, {}), 1)
        self.assertAlmostEqual(charm.hits_per_round(44, 1, -1, {}), 1 + 254 / 500)
        self.assertAlmostEqual(charm.hits_per_round(44, 1, 2, {}), 2 + 254 / 500)
        # Flurry and rampage don't fire on charmed pets.
        self.assertAlmostEqual(charm.hits_per_round(44, 1, -1, {5: [1], 3: [1]}), 1 + 254 / 500)
        da = 254 / 500
        self.assertAlmostEqual(charm.hits_per_round(44, 1, -1, {7: [1]}), 1 + da + (254 / 375) * (1 + da))
        # Level 60+ warriors and monks triple attack; other classes don't.
        da60 = min((250 + 60) / 500, 1)
        self.assertAlmostEqual(charm.hits_per_round(60, 1, -1, {}), 1 + da60 + da60 * 0.135)
        self.assertAlmostEqual(charm.hits_per_round(60, 2, -1, {}), 1 + da60)

    def test_ryklo(self):
        # Ryklo Rikes: level 44 warrior, 61-177 damage, 2.0 s delay.
        n = npc(level=44, mindmg=61, maxdmg=177, attack_delay=20)
        n["hits"] = charm.hits_per_round(44, 1, -1, {})
        self.assertAlmostEqual(charm.melee_dps(n), 119 * (1 + 254 / 500) / 2.0)

    def test_default_delay(self):
        n = npc(attack_delay=0, mindmg=10, maxdmg=10)
        n["hits"] = 1
        self.assertAlmostEqual(charm.melee_dps(n), 10 / 3.0)


class CharmRulesTest(unittest.TestCase):
    def check(self, sp, **kw):
        n = npc(**kw)
        n["immune"] = charm.charm_immunity(n)
        return charm.can_charm(sp, n)

    def test_level_cap(self):
        boltran = SPELLS[1]
        self.assertTrue(self.check(boltran, level=53))
        self.assertFalse(self.check(boltran, level=54))

    def test_body_types(self):
        dominate, animals, any_npc = SPELLS[3], SPELLS[5], SPELLS[0]
        self.assertFalse(self.check(dominate, body=1, level=20))  # a humanoid like Ryklo
        for body in (3, 8, 12):
            self.assertTrue(self.check(dominate, body=body, level=20))
        self.assertTrue(self.check(animals, body=21, level=20))
        self.assertFalse(self.check(animals, body=3, level=20))
        self.assertTrue(self.check(any_npc, body=21, level=20))

    def test_immunities(self):
        sp = SPELLS[1]
        self.assertFalse(self.check(sp, abilities={14: [1]}))
        self.assertFalse(self.check(sp, abilities={20: [1]}))
        self.assertFalse(self.check(sp, abilities={35: [1]}))
        self.assertFalse(self.check(sp, class_id=41))
        self.assertFalse(self.check(sp, class_id=40))
        self.assertFalse(self.check(sp, class_id=26))
        self.assertTrue(self.check(sp))
        veksar = [{"zone": "veksar", "zone_name": "Veksar", "expansion": 3, "points": 1, "chance": 100,
                   "respawn": 1, "live": True}]
        self.assertFalse(self.check(sp, level=50, spawns=veksar))
        self.assertTrue(self.check(sp, level=49, spawns=veksar))


class BestSpellTest(unittest.TestCase):
    def test_from_spellbook(self):
        self.assertEqual(charm.best_spell(SPELLS, ENC, 56, {300, 1705}), 1705)
        self.assertEqual(charm.best_spell(SPELLS, ENC, 56, {300}), 300)
        self.assertIsNone(charm.best_spell(SPELLS, ENC, 56, set()))

    def test_dictate_only_as_last_resort(self):
        self.assertEqual(charm.best_spell(SPELLS, ENC, 60, {300, 1705, 1707}), 1705)
        self.assertEqual(charm.best_spell(SPELLS, ENC, 60, {1707}), 1707)

    def test_new_character(self):
        self.assertEqual(charm.best_spell(SPELLS, ENC, 60), 1705)
        self.assertEqual(charm.best_spell(SPELLS, ENC, 40), 300)
        # Undead Pact has no scroll, so a new level 1 necromancer has no charm.
        self.assertIsNone(charm.best_spell(SPELLS, NEC, 10))
        self.assertEqual(charm.best_spell(SPELLS, NEC, 23), 196)


class ScoringTest(unittest.TestCase):
    def test_typical_and_outlier(self):
        npcs = [npc(id=i, name=f"mob {i}") for i in range(40)]
        npcs.append(npc(id=99, name="Big", raw_name="Big", hp=3000, mindmg=40, maxdmg=160, attack_delay=20))
        npcs.append(npc(id=100, name="raid boss", hp=10 ** 6, raid=True))
        charm.score_npcs(npcs)
        typical, big, boss = npcs[0], npcs[40], npcs[41]
        self.assertAlmostEqual(typical["score"], 1.0)
        self.assertGreater(big["score"], 2)
        self.assertAlmostEqual(big["hp_ratio"], 3.0)
        # Raid targets don't move the baseline, and no ratio counts for more than RATIO_CAP.
        self.assertAlmostEqual(boss["hp_ratio"], 1000)
        self.assertLessEqual(boss["score"], charm.RATIO_CAP)

    def test_baseline_pools_neighbouring_levels(self):
        npcs = [npc(id=i, level=30 + i % 3 - 1) for i in range(31)]
        charm.score_npcs(npcs)
        base = charm.level_baselines(npcs)
        self.assertIn(30, base)
        self.assertEqual(base[30]["hp"], 1000)

    def test_named(self):
        self.assertTrue(charm.is_named("Ryklo_Rikes"))
        self.assertTrue(charm.is_named("#Uenlarc"))
        self.assertFalse(charm.is_named("a_shadow_beast"))
        self.assertFalse(charm.is_named("An_Enthralled_Razorfiend"))


class MergeCopiesTest(unittest.TestCase):
    def test_same_stats_and_zones_merge(self):
        row = {"id": 0, "n": "Bzzazzt", "l": 50, "ml": 50, "c": 1, "b": 1, "hp": 1, "ac": 1, "mn": 1, "mx": 2, "dl": 30,
               "h": 1, "t": [], "raid": False}
        rows = [{**row, "id": 3, "z": [["air", "Plane of Sky", 0, 1, 50, 600, True]]},
                {**row, "id": 2, "z": [["air", "Plane of Sky", 0, 2, 100, 300, False]]},
                {**row, "id": 4, "z": [["fog", "Elsewhere", 0, 1, 100, 300, True]]}]
        out = sorted(charm.merge_copies(rows), key=lambda r: r["id"])
        self.assertEqual([r["id"] for r in out], [2, 4])
        self.assertEqual(out[0]["ids"], [3])
        self.assertEqual(out[0]["z"], [["air", "Plane of Sky", 0, 3, 100, 300, True]])


class LoadCharmSpellsTest(unittest.TestCase):
    def test_cap_from_charm_slot(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        cols = ", ".join(f"classes{c} INTEGER DEFAULT 255" for c in range(1, 16))
        conn.executescript(f"""
            CREATE TABLE spells_new (id INTEGER, name TEXT, targettype INTEGER, buffduration INTEGER, ResistDiff INTEGER,
                effectid1 INTEGER, max1 INTEGER, effectid2 INTEGER, max2 INTEGER, effectid3 INTEGER, max3 INTEGER, {cols});
            CREATE TABLE items (id INTEGER, Name TEXT, scrolleffect INTEGER);
            INSERT INTO spells_new (id, name, targettype, buffduration, ResistDiff, effectid1, max1, effectid2, max2,
                effectid3, max3, classes8) VALUES (750, 'Solon''s Bewitching Bravura', 5, 10, 0, 50, 0, 22, 51, 254, 0, 39);
            INSERT INTO spells_new (id, name, targettype, buffduration, ResistDiff, effectid1, max1, effectid2, max2,
                effectid3, max3, classes2) VALUES (1, 'Cleric thing', 5, 10, 0, 22, 20, 254, 0, 254, 0, 10);
            INSERT INTO items VALUES (5, 'Song: Solon''s Bewitching Bravura', 750);
        """)
        spells = charm.load_charm_spells(conn)
        self.assertEqual([s["id"] for s in spells], [750])
        self.assertEqual(spells[0]["cap"], 51)
        self.assertEqual(spells[0]["levels"], {8: 39})
        self.assertTrue(spells[0]["scroll"])


@unittest.skipUnless(common.DEFAULT_DB.is_file(), "needs data/quarm.sqlite (./quarm fetch)")
class RealDatabaseTest(unittest.TestCase):
    """Checks against the real dump: Ryklo Rikes is the motivating example."""

    @classmethod
    def setUpClass(cls):
        cls.data = charm.gather(common.DEFAULT_DB, None, Path("/nonexistent"))

    def top(self, spell_id, low):
        sp = next(s for s in self.data["spells"] if s["id"] == spell_id)
        return [r["n"] for r in self.data["npcs"] if not r["raid"] and r["l"] >= low and any(z[6] for z in r["z"])
                and charm.can_charm(sp, {"immune": "", "body": r["b"], "level": r["l"]})]

    def test_ryklo_near_top_for_boltrans(self):
        self.assertIn("Ryklo Rikes", self.top(1705, 53 - charm.LEVEL_WINDOW)[:5])

    def test_ryklo_not_undead(self):
        self.assertNotIn("Ryklo Rikes", self.top(198, 1))

    def test_new_characters(self):
        new = {c["class_id"]: c["spell"] for c in self.data["characters"] if c.get("new")}
        self.assertEqual(set(new), set(charm.CHARM_CLASSES))
        self.assertEqual(new[ENC], 1705)


class RenderTest(unittest.TestCase):
    def test_placeholders(self):
        static = charm.render_html({"npcs": [], "note": "</script>"})
        self.assertNotIn("/*DATA*/null", static)
        self.assertNotIn("/*THEME*/", static)
        self.assertNotIn('"</script>"', static)
        self.assertIn('class="home"', charm.render_html({}, nav=charm.web.HOME_LINK))


if __name__ == "__main__":
    unittest.main()
