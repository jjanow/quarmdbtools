"""Tests for the name-search FTS5 tables built by build_fts() in scripts/mariadb_to_sqlite.py.

Run from the repo root: python3 -m unittest discover tests
"""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mariadb_to_sqlite.py"
sys.path.insert(0, str(SCRIPT.parent))

from mariadb_to_sqlite import build_fts  # noqa: E402

# Mirrors the converted schema: items has no PRIMARY KEY (only a unique index on id), so its
# rowid is not its id; npc_types and spells_new have INTEGER PRIMARY KEY ids.
SCHEMA = """
CREATE TABLE items (id INTEGER NOT NULL DEFAULT 0, Name TEXT NOT NULL DEFAULT '' COLLATE NOCASE);
CREATE UNIQUE INDEX items__ID ON items (id);
CREATE TABLE npc_types (id INTEGER NOT NULL, name TEXT NOT NULL COLLATE NOCASE, PRIMARY KEY (id));
CREATE TABLE spells_new (id INTEGER NOT NULL DEFAULT 0, name TEXT DEFAULT NULL COLLATE NOCASE, PRIMARY KEY (id));
"""
ITEMS = [
    (3860, "Glyphed Skin Sample - Wrist, Right\0"),
    (1001, "Cloth Cap"),
    (14453, "Kilva's Skin of Flame"),
    (5482, "Fire Blossom"),
    (2000, "Fire"),
]
NPCS = [
    (2002, "a_gnoll_pup"),
    (14119, "#Shaman_Ren`Rex"),
    (3000, "Fippy_Darkpaw"),
]
SPELLS = [(58, "Elementalkin: Earth"), (316, "Elementalkin: Fire"), (999, None), (998, "")]


class BuildFtsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.sqlite"
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.executescript(SCHEMA)
        # Insert items with explicit rowids that differ from their ids, so any rowid/id
        # confusion in the FTS join would return the wrong row.
        self.conn.executemany(
            "INSERT INTO items (rowid, id, Name) VALUES (?, ?, ?)",
            [(n + 1, i, name) for n, (i, name) in enumerate(ITEMS)],
        )
        self.conn.executemany("INSERT INTO npc_types VALUES (?, ?)", NPCS)
        self.conn.executemany("INSERT INTO spells_new VALUES (?, ?)", SPELLS)
        self.conn.commit()

    def match(self, table, query):
        fts = table + "_fts"
        return [
            r[0] for r in self.conn.execute(
                f"SELECT t.id FROM {fts} f JOIN {table} t ON t.id = f.id WHERE {fts} MATCH ? ORDER BY rank",
                (query,),
            )
        ]

    def test_builds_all_tables(self):
        self.assertEqual(build_fts(self.conn), ["items_fts", "npc_types_fts", "spells_new_fts"])
        self.conn.commit()
        count = lambda t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: E731
        self.assertEqual(count("items_fts"), len(ITEMS))
        self.assertEqual(count("npc_types_fts"), len(NPCS))
        self.assertEqual(count("spells_new_fts"), 2)  # NULL and empty names skipped

    def test_join_uses_id_not_rowid(self):
        build_fts(self.conn)
        self.assertEqual(self.match("items", "cloth cap"), [1001])
        row = self.conn.execute(
            "SELECT i.id, i.Name FROM items_fts f JOIN items i ON i.id = f.id WHERE items_fts MATCH 'blossom'"
        ).fetchone()
        self.assertEqual(row, (5482, "Fire Blossom"))

    def test_words_in_any_order_and_ranking(self):
        build_fts(self.conn)
        self.assertEqual(self.match("items", "flame skin"), [14453])
        # The shorter exact name ranks first under bm25.
        self.assertEqual(self.match("items", "fire"), [2000, 5482])

    def test_prefix_search(self):
        build_fts(self.conn)
        self.assertEqual(self.match("items", "flam*"), [14453])
        self.assertEqual(sorted(self.match("spells_new", "elementalk*")), [58, 316])
        self.assertEqual(self.match("npc_types", "fip*"), [3000])

    def test_underscore_and_punctuation_are_separators(self):
        build_fts(self.conn)
        self.assertEqual(self.match("npc_types", "gnoll"), [2002])
        self.assertEqual(self.match("npc_types", "pup gnoll"), [2002])
        self.assertEqual(self.match("npc_types", "a_gnoll_pup"), [2002])
        self.assertEqual(self.match("npc_types", "shaman"), [14119])
        self.assertEqual(self.match("npc_types", "rex"), [14119])

    def test_trailing_nul_stripped(self):
        build_fts(self.conn)
        name = self.conn.execute("SELECT name FROM items_fts WHERE id = 3860").fetchone()[0]
        self.assertEqual(name, "Glyphed Skin Sample - Wrist, Right")
        self.assertEqual(self.match("items", '"wrist right"'), [3860])

    def test_idempotent(self):
        build_fts(self.conn)
        build_fts(self.conn)
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'items_fts'").fetchone()[0], 1
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM items_fts").fetchone()[0], len(ITEMS))
        self.assertEqual(self.match("items", "cap"), [1001])

    def test_rebuild_picks_up_changes(self):
        build_fts(self.conn)
        self.conn.execute("INSERT INTO items VALUES (7, 'Rusty Cap')")
        build_fts(self.conn)
        self.assertEqual(sorted(self.match("items", "cap")), [7, 1001])

    def test_missing_source_table_skipped(self):
        build_fts(self.conn)
        self.conn.execute("DROP TABLE items")
        self.conn.execute("DROP TABLE spells_new")
        self.assertEqual(build_fts(self.conn), ["npc_types_fts"])
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_fts'")}
        self.assertEqual(names, {"npc_types_fts"})  # stale FTS tables were dropped too

    def test_empty_database(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self.assertEqual(build_fts(conn), [])

    def test_works_inside_open_transaction(self):
        conn = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(conn.close)
        conn.execute("BEGIN")
        build_fts(conn)
        conn.execute("ROLLBACK")
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'items_fts'").fetchone())

    def test_duplicate_id_fails_and_rolls_back(self):
        self.conn.execute("DROP INDEX items__ID")
        self.conn.execute("INSERT INTO items VALUES (1001, 'Second Cap')")
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            build_fts(self.conn)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'items_fts'").fetchone())

    def test_fts_only_cli(self):
        self.conn.close()
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--fts-only", str(self.path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        conn = sqlite3.connect(self.path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT id FROM npc_types_fts WHERE npc_types_fts MATCH 'gnoll'").fetchall(), [(2002,)])


if __name__ == "__main__":
    unittest.main()
