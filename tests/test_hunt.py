"""Tests for quarmtools/tools/hunt.py: con colors, kill experience, faction safety and the zone ranking.

Run from the repo root: python3 -m unittest discover tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quarmtools import common  # noqa: E402
from quarmtools.tools import charm, hunt  # noqa: E402


class ConTest(unittest.TestCase):
    def test_brackets_follow_get_level_con(self):
        self.assertEqual(hunt.con(56, 56), "white")
        self.assertEqual(hunt.con(56, 58), "yellow")
        self.assertEqual(hunt.con(56, 59), "red")
        self.assertEqual(hunt.con(56, 42), "blue")
        self.assertEqual(hunt.con(56, 41), "lightblue")
        self.assertEqual(hunt.con(56, 36), "green")
        # The green line tightens again above 60.
        self.assertEqual(hunt.con(60, 39), "green")
        self.assertEqual(hunt.con(61, 42), "green")
        self.assertEqual(hunt.con(65, 49), "green")
        self.assertEqual(hunt.con(65, 50), "lightblue")
        # Low levels have no light blue band.
        self.assertEqual(hunt.con(5, 2), "blue")
        self.assertEqual(hunt.con(5, 1), "green")


class ExperienceTest(unittest.TestCase):
    def test_level_mod_and_span(self):
        self.assertEqual(hunt.exp_for_level(1), 0)
        self.assertEqual(hunt.exp_for_level(11), 10 ** 3 * 1000)
        self.assertAlmostEqual(hunt.exp_for_level(61), 60 ** 3 * 1000 * 3.0)
        self.assertAlmostEqual(hunt.exp_for_level(20, race=9), 19 ** 3 * 1200)  # troll
        # 59 and 60 share a level mod, so level 60 is short.
        self.assertLess(hunt.level_span(60), hunt.level_span(59))

    def test_multipliers(self):
        self.assertEqual(hunt.kill_multiplier(56, 36), 0)  # green
        self.assertEqual(hunt.kill_multiplier(30, 30), 1.0)
        self.assertEqual(hunt.kill_multiplier(60, 60), 2.6)
        self.assertAlmostEqual(hunt.kill_multiplier(60, 55), 1.95)
        self.assertEqual(hunt.kill_multiplier(65, 70), 3.0)  # capped
        # Below 60: cap 2.6 - 0.16 per level under 60, less 0.13 - 0.013x per level the mob is under you.
        self.assertAlmostEqual(hunt.kill_multiplier(56, 51), 1.96 - 5 * 0.078)
        self.assertAlmostEqual(hunt.kill_multiplier(56, 58), 1.96 + 2 * 0.039)
        # No mob level bonus for NPCs of 45 and under, or more than 5 levels away.
        self.assertEqual(hunt.kill_multiplier(51, 45), 1.0)
        self.assertEqual(hunt.kill_multiplier(56, 50), 1.0)
        # Light blue: 20%, or 60%/80% for the top light blues above level 40.
        self.assertEqual(hunt.kill_multiplier(30, 21), 0.2)
        self.assertEqual(hunt.kill_multiplier(56, 41), 0.8)
        self.assertEqual(hunt.kill_multiplier(56, 38), 0.2)

    def test_kill_fraction(self):
        frac = hunt.kill_fraction(30, 30, 1.0)
        self.assertAlmostEqual(frac, 30 * 30 * 100 / hunt.level_span(30))
        self.assertAlmostEqual(hunt.kill_fraction(30, 30, 1.3), frac * 1.3)
        self.assertAlmostEqual(hunt.kill_fraction(30, 30, 1.0, exp_pct=50), frac / 2)
        self.assertEqual(hunt.kill_fraction(30, 30, 1.0, exp_pct=0), 0)
        self.assertEqual(hunt.kill_fraction(56, 30, 1.0), 0)
        self.assertEqual(hunt.kill_fraction(5, 20, 1.7), hunt.KILL_CAP)


FACTIONS = {"names": {1: "Guards", 2: "Orcs", 3: "Hidden"}, "sets": {10: [[1, -5], [2, 3]], 20: [[2, -10]]},
            "matters": {1}, "base": {1: 0, 2: 0}, "mods": {1: {"r3": -800}, 2: {}}}


class FactionTest(unittest.TestCase):
    def test_target_faction(self):
        bad, other, good = hunt.target_faction(FACTIONS["sets"], FACTIONS["matters"], 10)
        self.assertEqual((bad, other, good), ([(1, -5)], [], [(2, 3)]))
        self.assertEqual(hunt.target_faction(FACTIONS["sets"], FACTIONS["matters"], 20), ([], [(2, -10)], []))
        self.assertEqual(hunt.target_faction(FACTIONS["sets"], FACTIONS["matters"], 99), ([], [], []))

    def test_only_a_consider_switches_a_faction_off(self):
        erudite = {"race": 3, "class": 14, "deity": 210}
        known, starts = hunt.already_scowling(FACTIONS, erudite, {})
        self.assertEqual(known, {})
        self.assertIn(1, starts)
        known, starts = hunt.already_scowling(FACTIONS, erudite, {1: "scowling"})
        self.assertIn(1, known)
        self.assertEqual(starts, {})
        # A consider that isn't scowling wins over a scowling start.
        self.assertEqual(hunt.already_scowling(FACTIONS, erudite, {1: "indifferent"}), ({}, {}))

    def test_read_quarmy(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "X-Quarmy.txt"
            p.write_text("Character\tName\tLastName\tLevel\tClass\tRace\tGender\tDeity\n"
                         "Character\tX\tY\t56\t14\t3\t0\t210\n", encoding="utf-8")
            self.assertEqual(hunt.read_quarmy(p), {"level": 56, "class": 14, "race": 3, "deity": 210})
            self.assertEqual(hunt.read_quarmy(Path(d) / "missing.txt"), {})


def npc(**kw):
    base = {"id": 1, "name": "a mob", "level": 50, "hp": 5000, "dps": 20.0, "hp_regen": 10, "faction_id": 0,
            "exp_pct": 100, "abilities": {}, "raid": False, "body": 1, "class_id": 1,
            "spawns": [{"zone": "z", "points": 2, "respawn": 600, "chance": 100, "live": True}]}
    base.update(kw)
    return base


class TargetsTest(unittest.TestCase):
    def test_filters(self):
        rows = hunt.kill_targets([
            npc(id=1), npc(id=2, name=""), npc(id=3, abilities={35: [1]}), npc(id=4, exp_pct=0), npc(id=5, raid=True),
            npc(id=6, class_id=41), npc(id=7, hp=1), npc(id=8, body=11), npc(id=9, abilities={19: [1]}),
            npc(id=10, spawns=[{"zone": "z", "points": 1, "respawn": 1, "chance": 100, "live": False}]),
            npc(id=11, abilities={1: [1]}),
        ], charm.JUNK_BODIES)
        self.assertEqual([r["id"] for r in rows], [1, 11])
        self.assertEqual(rows[1]["fl"], 1)

    def test_kills_per_hour(self):
        self.assertEqual(hunt.kills_per_hour(["z", 2, 600, 100]), 12)
        self.assertEqual(hunt.kills_per_hour(["z", 2, 600, 25]), 3)
        self.assertEqual(hunt.kills_per_hour(["z", 3, None, 100]), 3)


class PlanTest(unittest.TestCase):
    PET = {"dps": 100, "hp": 10000, "rg": 60}

    def target(self, **kw):
        t = {"id": 1, "n": "a mob", "l": 56, "hp": 3000, "dps": 50, "f": 0, "x": 100, "z": [["z", 1, 360, 100]]}
        t.update(kw)
        return t

    def test_faction_and_strength_rules(self):
        safe, bad, strong = self.target(id=1), self.target(id=2, f=10), self.target(id=3, hp=100000)
        total, used = hunt.zone_plan(self.PET, [safe, bad, strong], "z", 1.0, 56, matters={1}, sets=FACTIONS["sets"])
        self.assertEqual([t["id"] for t, _, _ in used], [1])
        self.assertAlmostEqual(used[0][1], 10)  # one point, 6 minute respawn
        self.assertAlmostEqual(total, 10 * hunt.kill_fraction(56, 56, 1.0))
        # With nothing that matters the faction target is fine.
        _, used = hunt.zone_plan(self.PET, [safe, bad], "z", 1.0, 56, matters=set(), sets=FACTIONS["sets"])
        self.assertEqual(len(used), 2)

    def test_time_budget(self):
        # 30s fight + 1500 HP / 10 per s regen + 30s overhead = 210s a kill: about 17 kills an hour.
        t = self.target(z=[["z", 100, 60, 100]])
        _, used = hunt.zone_plan(self.PET, [t], "z", 1.0, 56)
        self.assertAlmostEqual(used[0][1], 3600 / 210)

    def test_other_zone_ignored(self):
        self.assertEqual(hunt.zone_plan(self.PET, [self.target(z=[["elsewhere", 1, 60, 100]])], "z", 1.0, 56),
                         (0.0, []))


class RankTest(unittest.TestCase):
    def data(self):
        pet = {"id": 100, "n": "a strong pet", "l": 50, "b": 1, "s": 2.0, "dps": 100, "hp": 10000, "rg": 60,
               "raid": False, "z": [["field", "", 0, 1, 100, 600, True, "outdoor"],
                                    ["cave", "", 0, 1, 100, 600, True, "dungeon"]]}
        targets = [{"id": i, "n": f"mob {i}", "l": 56, "hp": 3000, "dps": 50, "f": 0, "x": 100,
                    "z": [[zone, 1, 360, 100]]} for i, zone in enumerate(["field", "cave", "elsewhere"])]
        return {"npcs": [pet], "target_bodies": {5: None},
                "hunt": {"targets": targets, "sets": {}, "matters": [], "xp": {"hell": {}},
                         "zones": {"field": ["Field", "outdoor", 1.0, 0, False], "cave": ["Cave", "dungeon", 1.0, 0, False],
                                   "elsewhere": ["Elsewhere", "outdoor", 1.0, 0, False]}}}

    def test_same_zone_and_open_terrain(self):
        spell = {"targettype": 5, "cap": 53}
        ranked = hunt.rank_zones(self.data(), 56, spell)
        self.assertEqual([zone for _, zone, _, _ in ranked], ["field"])
        ranked = hunt.rank_zones(self.data(), 56, spell, terrains=("outdoor", "dungeon"))
        self.assertEqual(sorted(zone for _, zone, _, _ in ranked), ["cave", "field"])
        self.assertEqual(hunt.rank_zones(self.data(), 56, {"targettype": 5, "cap": 49}), [])


@unittest.skipUnless(common.DEFAULT_DB.is_file(), "no database")
class DatabaseTest(unittest.TestCase):
    """Calibration against the one known-good hunting ground: Ryklo Rikes in The Dawnshroud Peaks."""

    @classmethod
    def setUpClass(cls):
        missing = Path(tempfile.gettempdir()) / "no-such-quarm-dir"
        cls.data = charm.gather(common.DEFAULT_DB, missing, missing / "user.db")

    def test_dawnshroud_ranks_near_the_top_for_cajoling_whispers(self):
        spell = next(s for s in self.data["spells"] if s["name"] == "Cajoling Whispers")
        ranked = hunt.rank_zones(self.data, 44, spell)
        top = [zone for _, zone, _, _ in ranked[:3]]
        self.assertIn("dawnshroud", top)
        dawn = next(r for r in ranked if r[1] == "dawnshroud")
        self.assertEqual(dawn[2]["n"], "Ryklo Rikes")

    def test_faction_defaults(self):
        h = self.data["hunt"]
        names = {h["factions"][f] for f in h["matters"]}
        self.assertIn("Guards of Qeynos", names)
        self.assertIn("Kromrif", names)
        self.assertFalse({"KOS", "Indifferent"} & names)
        self.assertNotIn("DawnshroudOne", names)

    def test_zone_info(self):
        zones = self.data["hunt"]["zones"]
        name, terrain, mult, era, flag, zem, hot = zones["dawnshroud"]
        self.assertEqual((terrain, zem, hot, flag), ("outdoor", 1.3, True, False))
        self.assertAlmostEqual(mult, 1.3 + 0.67)
        self.assertTrue(zones["bothunder"][4])
        self.assertTrue(zones["postorms"][4])
