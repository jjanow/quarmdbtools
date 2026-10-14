"""Tests for schema conversion in scripts/mariadb_to_sqlite.py (Table, Converter, main).

Covers column types and options, keys, table-end options, the _column_types and _meta
tables, error reporting, and a small end-to-end dump.

Most fixtures are CREATE TABLE statements copied verbatim from a real Project Quarm dump
(the comment above each one names the source file). A few features that the dump does not
use (prefix-length keys, non-ci collations, table comments) are synthesized in the same
style. The INSERT rows for server tables (aa_actions, grid_entries, rule_values, variables)
are real; rows for player tables are made up.

Run from the repo root: python3 -m unittest discover tests
"""
import contextlib
import datetime
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mariadb_to_sqlite  # noqa: E402
from mariadb_to_sqlite import Converter, ConvertError  # noqa: E402

# main() creates this before converting; the Converter class assumes it exists.
COLUMN_TYPES_DDL = (
    "CREATE TABLE _column_types (table_name TEXT, column_name TEXT, mariadb_type TEXT, "
    "PRIMARY KEY (table_name, column_name))"
)

# Header lines as mariadb-dump writes them. The sandbox line really ends in a space.
SANDBOX_LINE = "/*M!999999\\- enable the sandbox mode */ "
CS_SAVE = (
    "/*!40101 SET @saved_cs_client     = @@character_set_client */;\n"
    "/*!40101 SET character_set_client = utf8mb4 */;"
)
CS_RESTORE = "/*!40101 SET character_set_client = @saved_cs_client */;"

# --- Real DDL from the dump -------------------------------------------------------------

# from player_tables.sql:4
ACCOUNT_DDL = """\
CREATE TABLE `account` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(30) NOT NULL DEFAULT '',
  `charname` varchar(64) NOT NULL DEFAULT '',
  `sharedplat` int(11) unsigned NOT NULL DEFAULT 0,
  `password` varchar(50) NOT NULL DEFAULT '',
  `status` int(5) NOT NULL DEFAULT 0,
  `lsaccount_id` int(11) unsigned DEFAULT NULL,
  `forum_id` int(10) NOT NULL DEFAULT 0,
  `gmspeed` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `revoked` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `karma` int(5) unsigned NOT NULL DEFAULT 0,
  `minilogin_ip` varchar(32) NOT NULL DEFAULT '',
  `hideme` tinyint(4) NOT NULL DEFAULT 0,
  `rulesflag` tinyint(1) unsigned NOT NULL DEFAULT 0,
  `suspendeduntil` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `time_creation` int(10) unsigned NOT NULL DEFAULT 0,
  `expansion` tinyint(4) NOT NULL DEFAULT 12,
  `ban_reason` text DEFAULT NULL,
  `suspend_reason` text DEFAULT NULL,
  `active` tinyint(4) NOT NULL DEFAULT 0,
  `ip_exemption_multiplier` int(5) DEFAULT 1,
  `gminvul` tinyint(4) NOT NULL DEFAULT 0,
  `flymode` tinyint(4) NOT NULL DEFAULT 0,
  `ignore_tells` tinyint(4) NOT NULL DEFAULT 0,
  `mule` tinyint(4) NOT NULL DEFAULT 0,
  `revokeduntil` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `force_guild_id` int(11) NOT NULL DEFAULT 0,
  `forum_name` varchar(32) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`) USING BTREE,
  UNIQUE KEY `lsaccount_id` (`lsaccount_id`) USING BTREE,
  KEY `forum_name` (`forum_name`) USING BTREE
) ENGINE=MyISAM AUTO_INCREMENT=125011 DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""

# from player_tables.sql:594
FRIENDS_DDL = """\
CREATE TABLE `friends` (
  `charid` int(10) unsigned NOT NULL,
  `type` tinyint(1) unsigned NOT NULL DEFAULT 1 COMMENT '1 = Friend, 0 = Ignore',
  `name` varchar(64) NOT NULL,
  PRIMARY KEY (`charid`,`type`,`name`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;"""

# from player_tables.sql:1065 (the whole table: one nullable ON UPDATE timestamp)
MAIL_DDL = """\
CREATE TABLE `mail` (
  `time` timestamp NULL DEFAULT NULL ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;"""

# from player_tables.sql:1071
QS_PLAYER_SPEECH_DDL = """\
CREATE TABLE `qs_player_speech` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `from` varchar(64) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL,
  `to` varchar(325) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL,
  `message` varchar(256) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL,
  `minstatus` smallint(5) NOT NULL,
  `guilddbid` int(11) NOT NULL,
  `type` int(11) NOT NULL,
  `characterid` int(11) NOT NULL,
  `groupid` int(11) NOT NULL,
  `timerecorded` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `timerecorded_TS` (`timerecorded`) USING HASH,
  KEY `name_from` (`from`) USING BTREE,
  KEY `character_id_from` (`characterid`) USING BTREE,
  KEY `name_to` (`to`) USING BTREE,
  FULLTEXT KEY `character_id_to` (`to`)
) ENGINE=InnoDB AUTO_INCREMENT=328103157 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci ROW_FORMAT=COMPRESSED;"""

# from data_tables.sql:241
LAUNCHER_ZONES_DDL = """\
CREATE TABLE `launcher_zones` (
  `launcher` varchar(64) NOT NULL DEFAULT '',
  `zone` varchar(32) NOT NULL DEFAULT '',
  `port` mediumint(9) NOT NULL DEFAULT 0,
  `enabled` tinyint(1) unsigned zerofill DEFAULT 0,
  `expansion` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`launcher`,`zone`)
) ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""

# from data_tables.sql:1031
VARIABLES_DDL = """\
CREATE TABLE `variables` (
  `varname` varchar(25) NOT NULL DEFAULT '',
  `value` text NOT NULL,
  `information` text NOT NULL,
  `ts` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`varname`)
) ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""

# from data_tables.sql (command_settings): PRIMARY KEY and UNIQUE KEY on the same column
COMMAND_SETTINGS_DDL = """\
CREATE TABLE `command_settings` (
  `command` varchar(128) NOT NULL DEFAULT '',
  `access` int(11) NOT NULL DEFAULT 0,
  `aliases` varchar(256) NOT NULL DEFAULT '',
  PRIMARY KEY (`command`) USING BTREE,
  UNIQUE KEY `UK_command_settings_1` (`command`) USING BTREE
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""

# from data_tables.sql (rule_values)
RULE_VALUES_DDL = """\
CREATE TABLE `rule_values` (
  `ruleset_id` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `rule_name` varchar(256) NOT NULL DEFAULT '',
  `rule_value` varchar(256) NOT NULL DEFAULT '',
  `notes` varchar(1024) NOT NULL DEFAULT '',
  PRIMARY KEY (`ruleset_id`,`rule_name`),
  KEY `ruleset_id` (`ruleset_id`) USING BTREE
) ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""

# from quarm.sql:1 (aa_actions): composite primary key, unsigned columns
AA_ACTIONS_DDL = """\
CREATE TABLE `aa_actions` (
  `aaid` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `rank` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `reuse_time` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `spell_id` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `target` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `nonspell_action` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `nonspell_mana` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `nonspell_duration` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `redux_aa` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `redux_rate` tinyint(4) NOT NULL DEFAULT 0,
  `redux_aa2` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `redux_rate2` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`aaid`,`rank`)
) ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""

# from quarm.sql (grid_entries): primary key order differs from column order
GRID_ENTRIES_DDL = """\
CREATE TABLE `grid_entries` (
  `gridid` int(10) NOT NULL DEFAULT 0,
  `zoneid` int(10) NOT NULL DEFAULT 0,
  `number` int(10) NOT NULL DEFAULT 0,
  `x` float NOT NULL DEFAULT 0,
  `y` float NOT NULL DEFAULT 0,
  `z` float NOT NULL DEFAULT 0,
  `heading` float NOT NULL DEFAULT 0,
  `pause` int(10) NOT NULL DEFAULT 0,
  `centerpoint` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`zoneid`,`gridid`,`number`)
) ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""

# One column of every type family. The column lines are copied from the dump where it
# uses the type (quarm.sql/player_tables.sql); time, year, bit, binary, varbinary,
# longtext and the other blobs are not in the dump and follow mariadb-dump's format.
TYPE_SAMPLER_DDL = """\
CREATE TABLE `type_sampler` (
  `c_tinyint` tinyint(4) NOT NULL DEFAULT 0,
  `c_smallint` smallint(5) NOT NULL DEFAULT 0,
  `c_mediumint` mediumint(8) unsigned NOT NULL DEFAULT 0,
  `c_int` int(11) NOT NULL DEFAULT 0,
  `c_bigint` bigint(20) unsigned NOT NULL DEFAULT 0,
  `c_bit` bit(1) DEFAULT NULL,
  `c_float` float NOT NULL DEFAULT -1,
  `c_float_md` float(14,6) NOT NULL DEFAULT 0.000000,
  `c_double` double DEFAULT NULL,
  `c_decimal` decimal(6,2) NOT NULL DEFAULT 0.00,
  `c_char` char(16) NOT NULL DEFAULT '',
  `c_varchar` varchar(64) NOT NULL DEFAULT 'INVALID',
  `c_tinytext` tinytext DEFAULT NULL,
  `c_text` text DEFAULT NULL,
  `c_mediumtext` mediumtext DEFAULT NULL,
  `c_longtext` longtext DEFAULT NULL,
  `c_date` date DEFAULT NULL,
  `c_datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `c_timestamp` timestamp NOT NULL DEFAULT current_timestamp(),
  `c_time` time DEFAULT NULL,
  `c_year` year(4) DEFAULT NULL,
  `c_binary` binary(4) DEFAULT NULL,
  `c_varbinary` varbinary(32) DEFAULT NULL,
  `c_tinyblob` tinyblob DEFAULT NULL,
  `c_blob` blob DEFAULT NULL,
  `c_mediumblob` mediumblob DEFAULT NULL,
  `c_longblob` longblob DEFAULT NULL
) ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;"""


def write_sql(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def q(name):
    return '"' + name.replace('"', '""') + '"'


class ConverterTestCase(unittest.TestCase):
    """Runs the Converter class on SQL text written to a temp file, into an in-memory db."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.conn.close)
        self.conn.execute(COLUMN_TYPES_DDL)
        self.converter = Converter(self.conn)

    def load(self, *statements, name="quarm.sql"):
        path = self.dir / name
        write_sql(path, "\n".join(statements) + "\n")
        self.converter.convert_file(path)
        self.converter.create_indexes()

    def columns(self, table):
        """{column: (declared type, notnull, default, pk position)} from PRAGMA table_info."""
        rows = self.conn.execute(f"PRAGMA table_info({q(table)})").fetchall()
        return {name: (ctype, notnull, dflt, pk) for _, name, ctype, notnull, dflt, pk in rows}

    def schema(self, table):
        return self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()[0]

    def indexes(self, table):
        """{index name: (unique, [columns])} for indexes made with CREATE INDEX."""
        result = {}
        for _, name, unique, origin, _ in self.conn.execute(f"PRAGMA index_list({q(table)})"):
            if origin == "c":
                cols = [r[2] for r in self.conn.execute(f"PRAGMA index_info({q(name)})")]
                result[name] = (bool(unique), cols)
        return result

    def mariadb_types(self, table):
        return dict(self.conn.execute(
            "SELECT column_name, mariadb_type FROM _column_types WHERE table_name = ?", (table,)))

    def compares_nocase(self, table, column, **other_values):
        """Insert 'Cloth Cap' into column and report whether `= 'CLOTH CAP'` finds it."""
        values = dict(other_values, **{column: "Cloth Cap"})
        names = ", ".join(map(q, values))
        marks = ", ".join("?" * len(values))
        self.conn.execute("SAVEPOINT probe")
        try:
            rowid = self.conn.execute(
                f"INSERT INTO {q(table)} ({names}) VALUES ({marks})", list(values.values())
            ).lastrowid
            found = self.conn.execute(
                f"SELECT count(*) FROM {q(table)} WHERE rowid = ? AND {q(column)} = 'CLOTH CAP'",
                (rowid,),
            ).fetchone()[0]
        finally:
            self.conn.execute("ROLLBACK TO probe")
            self.conn.execute("RELEASE probe")
        return found == 1


class ColumnTypeTest(ConverterTestCase):
    def test_affinity_for_each_type_family(self):
        self.load(TYPE_SAMPLER_DDL)
        expected = {
            "c_tinyint": "INTEGER", "c_smallint": "INTEGER", "c_mediumint": "INTEGER",
            "c_int": "INTEGER", "c_bigint": "INTEGER", "c_bit": "INTEGER",
            "c_float": "REAL", "c_float_md": "REAL", "c_double": "REAL",
            "c_decimal": "NUMERIC",
            "c_char": "TEXT", "c_varchar": "TEXT", "c_tinytext": "TEXT", "c_text": "TEXT",
            "c_mediumtext": "TEXT", "c_longtext": "TEXT",
            "c_date": "TEXT", "c_datetime": "TEXT", "c_timestamp": "TEXT", "c_time": "TEXT",
            "c_year": "TEXT",
            "c_binary": "BLOB", "c_varbinary": "BLOB", "c_tinyblob": "BLOB", "c_blob": "BLOB",
            "c_mediumblob": "BLOB", "c_longblob": "BLOB",
        }
        actual = {name: info[0] for name, info in self.columns("type_sampler").items()}
        self.assertEqual(actual, expected)

    def test_affinity_coerces_values(self):
        self.load(TYPE_SAMPLER_DDL)
        self.conn.execute(
            "INSERT INTO type_sampler (c_int, c_float, c_decimal, c_varchar, c_datetime, c_blob) "
            "VALUES ('42', '1.5', '2.50', 42, '2026-09-27 22:44:00', ?)", (b"\x00\x01",))
        row = self.conn.execute(
            "SELECT typeof(c_int), typeof(c_float), typeof(c_decimal), typeof(c_varchar), "
            "typeof(c_datetime), typeof(c_blob) FROM type_sampler").fetchone()
        self.assertEqual(row, ("integer", "real", "real", "text", "text", "blob"))
        # REAL affinity turns an integer-valued float default/value into a real.
        self.assertEqual(self.conn.execute("SELECT c_float, typeof(c_float_md) FROM type_sampler").fetchone(),
                         (1.5, "real"))

    def test_column_types_table_keeps_original_types(self):
        self.load(TYPE_SAMPLER_DDL, ACCOUNT_DDL)
        sampler = self.mariadb_types("type_sampler")
        self.assertEqual(sampler["c_mediumint"], "mediumint(8) unsigned")
        self.assertEqual(sampler["c_bigint"], "bigint(20) unsigned")
        self.assertEqual(sampler["c_float"], "float")
        self.assertEqual(sampler["c_float_md"], "float(14,6)")
        self.assertEqual(sampler["c_double"], "double")
        self.assertEqual(sampler["c_decimal"], "decimal(6,2)")
        self.assertEqual(sampler["c_char"], "char(16)")
        self.assertEqual(sampler["c_text"], "text")
        self.assertEqual(sampler["c_year"], "year(4)")
        self.assertEqual(sampler["c_timestamp"], "timestamp")
        self.assertEqual(len(sampler), 27)

        account = self.mariadb_types("account")
        self.assertEqual(account["id"], "int(11)")
        self.assertEqual(account["sharedplat"], "int(11) unsigned")
        self.assertEqual(account["status"], "int(5)")
        self.assertEqual(account["gmspeed"], "tinyint(3) unsigned")
        self.assertEqual(account["name"], "varchar(30)")
        self.assertEqual(account["suspendeduntil"], "datetime")
        self.assertEqual(account["ban_reason"], "text")
        self.assertEqual(len(account), 28)

    def test_column_types_rows_replaced_on_drop_table(self):
        self.load(
            "DROP TABLE IF EXISTS `mail`;",
            MAIL_DDL,
            "DROP TABLE IF EXISTS `mail`;",
            "CREATE TABLE `mail` (",
            "  `id` bigint(20) unsigned NOT NULL,",
            "  PRIMARY KEY (`id`)",
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;",
        )
        self.assertEqual(self.mariadb_types("mail"), {"id": "bigint(20) unsigned"})
        self.assertEqual(list(self.columns("mail")), ["id"])


class ColumnOptionTest(ConverterTestCase):
    def test_not_null(self):
        self.load(ACCOUNT_DDL)
        cols = self.columns("account")
        self.assertEqual(cols["name"][1], 1)
        self.assertEqual(cols["sharedplat"][1], 1)
        self.assertEqual(cols["lsaccount_id"][1], 0)
        self.assertEqual(cols["ban_reason"][1], 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO account (name) VALUES (NULL)")

    def test_explicit_null_option(self):
        # `timestamp NULL DEFAULT NULL ON UPDATE ...`: NULL is allowed, default is NULL.
        self.load(MAIL_DDL)
        self.assertEqual(self.columns("mail"), {"time": ("TEXT", 0, "NULL", 0)})

    def test_numeric_defaults(self):
        self.load(ACCOUNT_DDL, TYPE_SAMPLER_DDL)
        account = self.columns("account")
        self.assertEqual(account["sharedplat"][2], "0")
        self.assertEqual(account["expansion"][2], "12")
        self.assertEqual(account["ip_exemption_multiplier"][2], "1")
        sampler = self.columns("type_sampler")
        self.assertEqual(sampler["c_float"][2], "-1")
        self.assertEqual(sampler["c_float_md"][2], "0.000000")
        self.assertEqual(sampler["c_decimal"][2], "0.00")
        # Defaults are applied as numbers, not text.
        self.conn.execute("INSERT INTO account (name) VALUES ('Bob')")
        self.assertEqual(
            self.conn.execute("SELECT expansion, typeof(expansion), ip_exemption_multiplier FROM account").fetchone(),
            (12, "integer", 1))
        self.conn.execute("INSERT INTO type_sampler (c_int) VALUES (1)")
        self.assertEqual(self.conn.execute("SELECT c_float, c_float_md FROM type_sampler").fetchone(), (-1.0, 0.0))

    def test_string_defaults(self):
        self.load(ACCOUNT_DDL, TYPE_SAMPLER_DDL)
        account = self.columns("account")
        self.assertEqual(account["name"][2], "''")
        self.assertEqual(account["suspendeduntil"][2], "'0000-00-00 00:00:00'")
        self.assertEqual(self.columns("type_sampler")["c_varchar"][2], "'INVALID'")
        self.conn.execute("INSERT INTO account (id) VALUES (1)")
        self.assertEqual(self.conn.execute("SELECT name, suspendeduntil FROM account").fetchone(),
                         ("", "0000-00-00 00:00:00"))

    def test_string_default_with_escapes(self):
        self.load(
            "CREATE TABLE `npc_note` (",
            "  `id` int(11) NOT NULL,",
            "  `greeting` varchar(64) NOT NULL DEFAULT 'Hail, it''s a \\'fine\\' day',",
            "  `path` varchar(64) NOT NULL DEFAULT 'C:\\\\eq\\\\',",
            "  PRIMARY KEY (`id`)",
            ") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        )
        self.conn.execute("INSERT INTO npc_note (id) VALUES (1)")
        self.assertEqual(self.conn.execute("SELECT greeting, path FROM npc_note").fetchone(),
                         ("Hail, it's a 'fine' day", "C:\\eq\\"))

    def test_default_null(self):
        self.load(ACCOUNT_DDL)
        cols = self.columns("account")
        self.assertEqual(cols["lsaccount_id"][2], "NULL")
        self.assertEqual(cols["ban_reason"][2], "NULL")
        # A column without a DEFAULT clause has no default at all.
        self.assertIsNone(cols["id"][2])

    def test_default_current_timestamp(self):
        self.load(VARIABLES_DDL)
        self.assertEqual(self.columns("variables")["ts"][2], "CURRENT_TIMESTAMP")
        self.conn.execute("INSERT INTO variables (varname, value, information) VALUES ('MOTD', 'hi', '')")
        ts = self.conn.execute("SELECT ts FROM variables").fetchone()[0]
        # Same text format MariaDB uses for timestamps.
        datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

    def test_on_update_is_dropped(self):
        self.load(VARIABLES_DDL, QS_PLAYER_SPEECH_DDL)
        for table in ("variables", "qs_player_speech"):
            with self.subTest(table=table):
                self.assertNotIn("ON UPDATE", self.schema(table).upper())
        self.assertEqual(self.columns("qs_player_speech")["timerecorded"], ("TEXT", 1, "CURRENT_TIMESTAMP", 0))
        triggers = self.conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0]
        self.assertEqual(triggers, 0)

    def test_auto_increment(self):
        self.load(ACCOUNT_DDL)
        self.assertNotIn("AUTO", self.schema("account").upper())
        self.assertEqual(self.columns("account")["id"], ("INTEGER", 1, None, 1))
        # The single INTEGER primary key is a rowid alias, so omitting it assigns max + 1.
        self.conn.execute("INSERT INTO account (id, name) VALUES (125010, 'Bob')")
        cur = self.conn.execute("INSERT INTO account (name) VALUES ('Alice')")
        self.assertEqual(cur.lastrowid, 125011)
        self.assertEqual(self.conn.execute("SELECT id FROM account WHERE name = 'Alice'").fetchone(), (125011,))

    def test_unsigned(self):
        self.load(AA_ACTIONS_DDL)
        types = self.mariadb_types("aa_actions")
        self.assertEqual(types["aaid"], "mediumint(8) unsigned")
        self.assertEqual(types["rank"], "tinyint(3) unsigned")
        self.assertEqual(types["redux_rate"], "tinyint(4)")
        self.assertEqual(self.columns("aa_actions")["aaid"][:3], ("INTEGER", 1, "0"))
        self.assertNotIn("unsigned", self.schema("aa_actions").lower())

    def test_zerofill_column_converts(self):
        self.load(LAUNCHER_ZONES_DDL)
        self.assertEqual(self.columns("launcher_zones")["enabled"], ("INTEGER", 0, "0", 0))
        self.assertTrue(self.mariadb_types("launcher_zones")["enabled"].startswith("tinyint(1) unsigned"))

    # _column_types keeps the original MariaDB type, including zerofill.
    def test_zerofill_is_kept_in_column_types(self):
        self.load(LAUNCHER_ZONES_DDL)
        self.assertEqual(self.mariadb_types("launcher_zones")["enabled"], "tinyint(1) unsigned zerofill")

    def test_comment_is_dropped(self):
        self.load(FRIENDS_DDL)
        self.assertEqual(self.columns("friends")["type"], ("INTEGER", 1, "1", 2))
        self.assertNotIn("Friend", self.schema("friends"))
        self.assertEqual(self.mariadb_types("friends")["type"], "tinyint(1) unsigned")

    def test_comment_with_quotes_and_commas(self):
        self.load(
            "CREATE TABLE `npc_note` (",
            "  `id` int(11) NOT NULL COMMENT 'id, not \\'npc_id\\'; it''s (unique)',",
            "  `note` varchar(64) DEFAULT NULL COMMENT 'free text',",
            "  PRIMARY KEY (`id`)",
            ") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        )
        self.assertEqual(self.columns("npc_note"), {
            "id": ("INTEGER", 1, None, 1),
            "note": ("TEXT", 0, "NULL", 0),
        })

    # "unsigned" inside a COMMENT or string DEFAULT is not the unsigned option.
    def test_unsigned_in_comment_is_not_an_option(self):
        self.load(
            "CREATE TABLE `npc_note` (",
            "  `delta` int(11) NOT NULL DEFAULT 0 COMMENT 'signed; was unsigned before 2024',",
            "  `label` varchar(16) NOT NULL DEFAULT 'unsigned'",
            ") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        )
        self.assertEqual(self.mariadb_types("npc_note"), {"delta": "int(11)", "label": "varchar(16)"})


class CollationTest(ConverterTestCase):
    def test_ci_table_collation_gives_nocase(self):
        self.load(ACCOUNT_DDL, FRIENDS_DDL)
        self.assertTrue(self.compares_nocase("account", "name"))
        self.assertTrue(self.compares_nocase("account", "ban_reason"))  # text
        self.assertTrue(self.compares_nocase("friends", "name", charid=1))  # utf8mb3_general_ci

    def test_ci_column_collation_gives_nocase(self):
        # Real DDL: per-column CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci.
        self.load(QS_PLAYER_SPEECH_DDL)
        others = dict(to="x", message="x", minstatus=0, guilddbid=0, type=0, characterid=0, groupid=0)
        self.assertTrue(self.compares_nocase("qs_player_speech", "from", **others))

    def test_every_text_type_gets_nocase(self):
        self.load(TYPE_SAMPLER_DDL)
        for column in ("c_char", "c_varchar", "c_tinytext", "c_text", "c_mediumtext", "c_longtext"):
            with self.subTest(column=column):
                self.assertTrue(self.compares_nocase("type_sampler", column))

    def test_non_text_columns_get_no_collation(self):
        self.load(TYPE_SAMPLER_DDL)
        self.assertFalse(self.compares_nocase("type_sampler", "c_datetime"))
        self.assertFalse(self.compares_nocase("type_sampler", "c_blob"))

    def test_bin_column_in_ci_table_is_case_sensitive(self):
        self.load(
            "CREATE TABLE `npc_note` (",
            "  `key_` varchar(64) CHARACTER SET latin1 COLLATE latin1_bin NOT NULL DEFAULT '',",
            "  `name` varchar(64) NOT NULL DEFAULT ''",
            ") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        )
        self.assertFalse(self.compares_nocase("npc_note", "key_"))
        self.assertTrue(self.compares_nocase("npc_note", "name"))

    def test_ci_column_in_bin_table_gets_nocase(self):
        self.load(
            "CREATE TABLE `npc_note` (",
            "  `key_` varchar(64) NOT NULL DEFAULT '',",
            "  `name` varchar(64) CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci NOT NULL DEFAULT ''",
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;",
        )
        self.assertFalse(self.compares_nocase("npc_note", "key_"))
        self.assertTrue(self.compares_nocase("npc_note", "name"))

    def test_unique_index_follows_nocase(self):
        # MariaDB rejects 'Bob' and 'BOB' under a _ci unique key; so should SQLite.
        self.load(ACCOUNT_DDL)
        self.conn.execute("INSERT INTO account (name) VALUES ('Bob')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO account (name) VALUES ('BOB')")


class KeyTest(ConverterTestCase):
    def test_single_column_primary_key(self):
        self.load(ACCOUNT_DDL)
        pks = {name: info[3] for name, info in self.columns("account").items() if info[3]}
        self.assertEqual(pks, {"id": 1})

    def test_composite_primary_key_follows_key_order(self):
        self.load(GRID_ENTRIES_DDL, FRIENDS_DDL)
        pks = {name: info[3] for name, info in self.columns("grid_entries").items() if info[3]}
        self.assertEqual(pks, {"zoneid": 1, "gridid": 2, "number": 3})
        pks = {name: info[3] for name, info in self.columns("friends").items() if info[3]}
        self.assertEqual(pks, {"charid": 1, "type": 2, "name": 3})
        self.conn.execute("INSERT INTO grid_entries (gridid, zoneid, number) VALUES (1, 1, 1)")
        self.conn.execute("INSERT INTO grid_entries (gridid, zoneid, number) VALUES (1, 2, 1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO grid_entries (gridid, zoneid, number) VALUES (1, 1, 1)")

    def test_primary_key_using_btree(self):
        self.load(COMMAND_SETTINGS_DDL)
        self.assertEqual(self.columns("command_settings")["command"][3], 1)
        self.assertEqual(self.indexes("command_settings"),
                         {"command_settings__UK_command_settings_1": (True, ["command"])})

    def test_unique_and_plain_keys(self):
        self.load(ACCOUNT_DDL)
        self.assertEqual(self.indexes("account"), {
            "account__name": (True, ["name"]),
            "account__lsaccount_id": (True, ["lsaccount_id"]),
            "account__forum_name": (False, ["forum_name"]),
        })

    def test_using_hash_and_fulltext_keys(self):
        self.load(QS_PLAYER_SPEECH_DDL)
        self.assertEqual(self.indexes("qs_player_speech"), {
            "qs_player_speech__timerecorded_TS": (False, ["timerecorded"]),
            "qs_player_speech__name_from": (False, ["from"]),
            "qs_player_speech__character_id_from": (False, ["characterid"]),
            "qs_player_speech__name_to": (False, ["to"]),
            "qs_player_speech__character_id_to": (False, ["to"]),  # FULLTEXT -> plain index
        })

    def test_composite_keys(self):
        self.load(RULE_VALUES_DDL)
        pks = {name: info[3] for name, info in self.columns("rule_values").items() if info[3]}
        self.assertEqual(pks, {"ruleset_id": 1, "rule_name": 2})
        self.assertEqual(self.indexes("rule_values"), {"rule_values__ruleset_id": (False, ["ruleset_id"])})

    def test_prefix_length_columns(self):
        self.load(
            "CREATE TABLE `npc_note` (",
            "  `id` int(11) NOT NULL,",
            "  `zone` varchar(32) NOT NULL DEFAULT '',",
            "  `name` varchar(64) NOT NULL DEFAULT '',",
            "  `body` text NOT NULL,",
            "  PRIMARY KEY (`id`),",
            "  UNIQUE KEY `zone_name` (`zone`,`name`(10)) USING BTREE,",
            "  KEY `body_prefix` (`body`(255)),",
            "  KEY `name_zone` (`name`(20),`zone`) USING HASH,",
            "  FULLTEXT KEY `body_ft` (`body`)",
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;",
        )
        self.assertEqual(self.indexes("npc_note"), {
            "npc_note__zone_name": (True, ["zone", "name"]),
            "npc_note__body_prefix": (False, ["body"]),
            "npc_note__name_zone": (False, ["name", "zone"]),
            "npc_note__body_ft": (False, ["body"]),
        })

    def test_key_names_are_prefixed_with_table(self):
        # Two tables with a key of the same name do not collide.
        self.load(
            ACCOUNT_DDL,
            "CREATE TABLE `account_flags` (",
            "  `p_accid` int(10) unsigned NOT NULL DEFAULT 0,",
            "  `name` varchar(50) NOT NULL,",
            "  PRIMARY KEY (`p_accid`,`name`),",
            "  KEY `name` (`name`)",
            ") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        )
        self.assertIn("account__name", self.indexes("account"))
        self.assertEqual(self.indexes("account_flags"), {"account_flags__name": (False, ["name"])})
        bare = self.conn.execute("SELECT count(*) FROM sqlite_master WHERE type = 'index' AND name = 'name'")
        self.assertEqual(bare.fetchone()[0], 0)

    def test_indexes_are_created_only_by_create_indexes(self):
        path = self.dir / "quarm.sql"
        write_sql(path, ACCOUNT_DDL + "\n")
        self.converter.convert_file(path)
        self.assertEqual(self.indexes("account"), {})
        self.converter.create_indexes()
        self.assertEqual(len(self.indexes("account")), 3)


class TableOptionTest(ConverterTestCase):
    TABLE_BODY = (
        "  `id` int(11) NOT NULL AUTO_INCREMENT,\n"
        "  `name` varchar(64) DEFAULT NULL,\n"
        "  PRIMARY KEY (`id`)"
    )

    # (table end line, whether text columns compare case-insensitively)
    CASES = [
        # Variants seen in the real dump:
        (") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;", True),
        (") ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;", True),
        (") ENGINE=MyISAM AUTO_INCREMENT=125011 DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;", True),
        (") ENGINE=MyISAM AUTO_INCREMENT=3 DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci PACK_KEYS=0;", True),
        (") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci ROW_FORMAT=DYNAMIC;", True),
        (") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci ROW_FORMAT=DYNAMIC;", True),
        (") ENGINE=InnoDB AUTO_INCREMENT=328103157 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci "
         "ROW_FORMAT=COMPRESSED;", True),
        # Not in the dump, in mariadb-dump's format:
        (") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;", False),
        (") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_general_cs;", False),
        (") ENGINE=Aria DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci PACK_KEYS=1 ROW_FORMAT=PAGE;", True),
        (") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci COMMENT='Spawn points, by zone';", True),
        (") ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin "
         "ROW_FORMAT=DYNAMIC COMMENT='';", False),
    ]

    def test_table_end_options(self):
        for i, (end, nocase) in enumerate(self.CASES):
            with self.subTest(end=end):
                self.load(f"CREATE TABLE `t{i}` (\n{self.TABLE_BODY}\n{end}", name=f"t{i}.sql")
                self.assertEqual(list(self.columns(f"t{i}")), ["id", "name"])
                self.assertEqual(self.compares_nocase(f"t{i}", "name"), nocase)
                schema = self.schema(f"t{i}").upper()
                for word in ("ENGINE", "CHARSET", "ROW_FORMAT", "PACK_KEYS", "COMMENT", "AUTO_INCREMENT"):
                    self.assertNotIn(word, schema)
            # create_indexes() runs over every table loaded so far; start fresh each time.
            self.converter = Converter(self.conn)

    # mariadb-dump writes a quote inside a table comment as ''.
    def test_table_comment_with_escaped_quote(self):
        self.load(f"CREATE TABLE `t` (\n{self.TABLE_BODY}\n"
                  ") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci COMMENT='Bob''s table';")
        self.assertEqual(list(self.columns("t")), ["id", "name"])


class ErrorTest(ConverterTestCase):
    def assertRejected(self, lines, lineno, message, name="player_tables.sql"):
        with self.assertRaises(ConvertError) as ctx:
            self.load(*lines, name=name)
        text = str(ctx.exception)
        self.assertIn(f"{name}:{lineno}: ", text)
        self.assertIn(message, text)
        return text

    def test_unknown_column_type(self):
        text = self.assertRejected([
            SANDBOX_LINE,
            CS_SAVE,
            "CREATE TABLE `character_data` (",
            "  `id` int(11) unsigned NOT NULL,",
            "  `expansion` enum('classic','kunark','velious') NOT NULL DEFAULT 'classic',",
            "  PRIMARY KEY (`id`)",
            ") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        ], 6, "unsupported column type 'enum'")
        self.assertIn("`expansion` enum(", text)  # the offending line is quoted

    def test_unknown_column_type_without_args(self):
        self.assertRejected([
            "CREATE TABLE `zone_points` (",
            "  `id` int(11) NOT NULL,",
            "  `location` point NOT NULL,",
            ") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        ], 3, "unsupported column type 'point'")

    def test_unknown_column_option(self):
        # How MariaDB dumps a JSON column.
        self.assertRejected([
            "CREATE TABLE `data_buckets` (",
            "  `id` bigint(11) unsigned NOT NULL AUTO_INCREMENT,",
            "  `value` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`value`)),",
            "  PRIMARY KEY (`id`)",
            ") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        ], 3, "unsupported column option 'CHECK (json_valid(`value`))'")

    def test_unknown_column_option_invisible(self):
        self.assertRejected([
            "CREATE TABLE `account` (",
            "  `id` int(11) NOT NULL AUTO_INCREMENT,",
            "  `secret` varchar(50) NOT NULL DEFAULT '' INVISIBLE,",
            "  PRIMARY KEY (`id`)",
            ") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        ], 3, "unsupported column option 'INVISIBLE'")

    def test_unknown_table_option(self):
        self.assertRejected([
            CS_SAVE,
            "CREATE TABLE `variables` (",
            "  `varname` varchar(25) NOT NULL DEFAULT '',",
            "  PRIMARY KEY (`varname`)",
            ") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci CHECKSUM=1;",
        ], 6, "unsupported table options", name="data_tables.sql")

    def test_unknown_table_option_stats(self):
        self.assertRejected([
            "CREATE TABLE `variables` (",
            "  `varname` varchar(25) NOT NULL DEFAULT '',",
            "  PRIMARY KEY (`varname`)",
            ") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci STATS_PERSISTENT=0;",
        ], 4, "unsupported table options", name="data_tables.sql")

    def test_unknown_table_definition_line(self):
        self.assertRejected([
            "CREATE TABLE `friends` (",
            "  `charid` int(10) unsigned NOT NULL,",
            "  KEY `charid` (`charid`),",
            "  CONSTRAINT `fk_char` FOREIGN KEY (`charid`) REFERENCES `character_data` (`id`)",
            ") ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci;",
        ], 4, "unsupported table definition line")

    def test_insert_with_wrong_column_count(self):
        self.assertRejected([
            FRIENDS_DDL,
            "INSERT INTO `friends` VALUES",
            "(1,1,'Bob'),",
            "(2,1);",
        ], 9, "row has 2 values, table 'friends' has 3 columns")

    def test_unexpected_end_of_file(self):
        with self.assertRaises(ConvertError) as ctx:
            self.load("CREATE TABLE `friends` (", "  `charid` int(10) unsigned NOT NULL,", name="login_tables.sql")
        self.assertIn("login_tables.sql: unexpected end of file", str(ctx.exception))


# --- End to end ---------------------------------------------------------------------------

E2E_QUARM = "\n".join([
    SANDBOX_LINE,
    CS_SAVE,
    AA_ACTIONS_DDL,
    CS_RESTORE,
    "INSERT INTO `aa_actions` VALUES",
    "(128,0,4320,0,1,2,0,100000,782,10,0,0),",
    "(129,0,129600,2738,2,0,0,0,0,0,0,0),",
    "(133,0,4320,2776,1,0,0,0,1039,10,0,0),",
    "(133,1,4320,2777,1,0,0,0,1039,10,0,0);",
    CS_SAVE,
    GRID_ENTRIES_DDL,
    CS_RESTORE,
    # Several rows on one line.
    "INSERT INTO `grid_entries` VALUES (0,0,0,0,0,0,-1,0,0),(1,1,1,-119,120,3.128,195,325,0),"
    "(1,1,2,-200.574,132.097,3.127,-1,0,0);",
    "",
])

E2E_PLAYER = "\n".join([
    SANDBOX_LINE,
    CS_SAVE,
    ACCOUNT_DDL,
    CS_RESTORE,
    "INSERT INTO `account` VALUES",
    "(1,'Bob\\'s Alt','',0,'',0,NULL,0,0,0,0,'',0,0,'0000-00-00 00:00:00',1700000000,12,"
    "'Line one\\r\\nLine two',NULL,0,1,0,0,0,0,'0000-00-00 00:00:00',0,'C:\\\\eq\\\\'),",
    "(2,'it''s me','Tester',4294967295,'',0,5,0,0,0,0,'',0,0,'2026-01-02 03:04:05',0,12,"
    "NULL,NULL,1,1,0,0,0,0,'0000-00-00 00:00:00',0,'say \\\"hi\\\"');",
    CS_SAVE,
    FRIENDS_DDL,
    CS_RESTORE,
    CS_SAVE,
    QS_PLAYER_SPEECH_DDL,
    CS_RESTORE,
    "INSERT INTO `qs_player_speech` VALUES",
    "(1,'Bob','Alice','nul\\0byte',0,0,7,1,0,'2026-09-01 12:00:00');",
    "",
])

E2E_LOGIN = "\n".join([SANDBOX_LINE, ""])

E2E_DATA = "\n".join([
    SANDBOX_LINE,
    CS_SAVE,
    RULE_VALUES_DDL,
    CS_RESTORE,
    "INSERT INTO `rule_values` VALUES",
    "(1,'Character:DefaultExpansions','15','When a new account is created, this is the default "
    "bitmask expansions it is given. 1 Kunark 2 Velious 4 Luclin 8 PoP.'),",
    "(1,'AlKabor:AllowCharmPetRaidTanks','false','AK behavior is true.  If false, NPCs will ignore "
    "charmed pets once MaxEntitiesCharmTanks players get on an NPC\\'s hate list as per April 2003 patch.'),",
    "(1,'NPC:ReturnNonQuestItems','true','Returns items on NPCs that don\\'t have an EVENT_TRADE sub "
    "in their script or have KOS faction with the player');",
    CS_SAVE,
    VARIABLES_DDL,
    CS_RESTORE,
    "INSERT INTO `variables` VALUES",
    "('AAXPMod','0.75','AA Experience multipler. Increase to increase exp rate','2015-02-03 01:59:49'),",
    "('MerchantsKeepItems','1','Merchants keep items sold to them | 0 - off | 1 - on |','2015-02-03 01:59:49');",
    "",
])

DUMP_FILES = {
    "quarm.sql": E2E_QUARM,
    "player_tables.sql": E2E_PLAYER,
    "login_tables.sql": E2E_LOGIN,
    "data_tables.sql": E2E_DATA,
}


def run_main(*argv):
    """Run the converter's main() with argv; return its stdout."""
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["mariadb_to_sqlite.py", *argv]), contextlib.redirect_stdout(out):
        mariadb_to_sqlite.main()
    return out.getvalue()


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.src = tmp / "dump"
        cls.src.mkdir()
        for name, text in DUMP_FILES.items():
            write_sql(cls.src / name, text)
        (cls.src / "VERSION").write_text("2026-09-27-22_44\n")
        cls.out = tmp / "out" / "quarm.sqlite"  # main() creates the parent directory

        real_db = mariadb_to_sqlite.DEFAULT_OUT
        before = real_db.stat().st_mtime_ns if real_db.exists() else None
        cls.stdout = run_main("--src", str(cls.src), "--out", str(cls.out))
        after = real_db.stat().st_mtime_ns if real_db.exists() else None
        assert before == after, "end-to-end test touched data/quarm.sqlite"

        cls.conn = sqlite3.connect(str(cls.out))

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls._tmp.cleanup()

    def query(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def test_output_file_written_and_tmp_removed(self):
        self.assertTrue(self.out.is_file())
        self.assertFalse(self.out.with_name(self.out.name + ".tmp").exists())
        self.assertIn("Wrote", self.stdout)
        self.assertIn("2026-09-27-22_44", self.stdout)

    def test_source_tables_exist(self):
        # Other passes may add tables, so only check that these are present.
        tables = {r[0] for r in self.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        expected = {"_meta", "_column_types", "aa_actions", "grid_entries", "account", "friends",
                    "qs_player_speech", "rule_values", "variables"}
        self.assertLessEqual(expected, tables)

    def test_row_counts(self):
        counts = {
            "aa_actions": 4, "grid_entries": 3, "account": 2, "friends": 0,
            "qs_player_speech": 1, "rule_values": 3, "variables": 2,
        }
        for table, n in counts.items():
            with self.subTest(table=table):
                self.assertEqual(self.query(f"SELECT count(*) FROM {q(table)}"), [(n,)])

    def test_values_and_escapes(self):
        self.assertEqual(
            self.query("SELECT name, lsaccount_id, ban_reason, forum_name FROM account ORDER BY id"),
            [("Bob's Alt", None, "Line one\r\nLine two", "C:\\eq\\"),
             ("it's me", 5, None, 'say "hi"')])
        self.assertEqual(self.query("SELECT sharedplat, typeof(sharedplat) FROM account WHERE id = 2"),
                         [(4294967295, "integer")])
        self.assertEqual(self.query("SELECT message FROM qs_player_speech"), [("nul\0byte",)])
        notes = dict(self.query("SELECT rule_name, notes FROM rule_values"))
        self.assertIn("on an NPC's hate list", notes["AlKabor:AllowCharmPetRaidTanks"])
        self.assertTrue(notes["NPC:ReturnNonQuestItems"].startswith("Returns items on NPCs that don't"))
        self.assertEqual(self.query("SELECT value, ts FROM variables WHERE varname = 'MerchantsKeepItems'"),
                         [("1", "2015-02-03 01:59:49")])
        # Zero dates are kept verbatim.
        self.assertEqual(self.query("SELECT suspendeduntil FROM account WHERE id = 1"), [("0000-00-00 00:00:00",)])

    def test_numeric_values(self):
        self.assertEqual(
            self.query("SELECT x, y, z, heading, typeof(heading) FROM grid_entries ORDER BY number"),
            [(0.0, 0.0, 0.0, -1.0, "real"), (-119.0, 120.0, 3.128, 195.0, "real"),
             (-200.574, 132.097, 3.127, -1.0, "real")])
        self.assertEqual(self.query("SELECT reuse_time FROM aa_actions WHERE aaid = 129"), [(129600,)])

    def test_table_info(self):
        info = self.query("PRAGMA table_info(qs_player_speech)")
        self.assertEqual([(r[1], r[2], r[3], r[4], r[5]) for r in info], [
            ("id", "INTEGER", 1, None, 1),
            ("from", "TEXT", 1, None, 0),
            ("to", "TEXT", 1, None, 0),
            ("message", "TEXT", 1, None, 0),
            ("minstatus", "INTEGER", 1, None, 0),
            ("guilddbid", "INTEGER", 1, None, 0),
            ("type", "INTEGER", 1, None, 0),
            ("characterid", "INTEGER", 1, None, 0),
            ("groupid", "INTEGER", 1, None, 0),
            ("timerecorded", "TEXT", 1, "CURRENT_TIMESTAMP", 0),
        ])
        pk = [r[1] for r in sorted(self.query("PRAGMA table_info(grid_entries)"), key=lambda r: r[5]) if r[5]]
        self.assertEqual(pk, ["zoneid", "gridid", "number"])

    def test_indexes(self):
        rows = self.query("SELECT tbl_name, name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL "
                          "AND tbl_name IN ('account', 'qs_player_speech', 'rule_values')")
        self.assertEqual(sorted(rows), sorted([
            ("account", "account__name"),
            ("account", "account__lsaccount_id"),
            ("account", "account__forum_name"),
            ("qs_player_speech", "qs_player_speech__timerecorded_TS"),
            ("qs_player_speech", "qs_player_speech__name_from"),
            ("qs_player_speech", "qs_player_speech__character_id_from"),
            ("qs_player_speech", "qs_player_speech__name_to"),
            ("qs_player_speech", "qs_player_speech__character_id_to"),
            ("rule_values", "rule_values__ruleset_id"),
        ]))
        unique = {r[1]: r[2] for r in self.query("PRAGMA index_list(account)")}
        self.assertEqual(unique["account__name"], 1)
        self.assertEqual(unique["account__forum_name"], 0)
        # The query planner uses the converted index.
        plan = " ".join(r[3] for r in self.query("EXPLAIN QUERY PLAN SELECT * FROM account WHERE name = 'x'"))
        self.assertIn("account__name", plan)

    def test_nocase(self):
        self.assertEqual(self.query("SELECT id FROM account WHERE name = 'BOB''S ALT'"), [(1,)])
        self.assertEqual(self.query("SELECT rule_value FROM rule_values WHERE rule_name = 'npc:returnnonquestitems'"),
                         [("true",)])
        self.assertEqual(self.query("SELECT id FROM qs_player_speech WHERE \"from\" = 'bob'"), [(1,)])
        # Non-text columns compare normally.
        self.assertEqual(self.query("SELECT count(*) FROM account WHERE suspendeduntil = '2026-01-02 03:04:05'"),
                         [(1,)])

    def test_column_types_table(self):
        types = dict(((t, c), m) for t, c, m in self.query("SELECT * FROM _column_types"))
        self.assertEqual(types[("aa_actions", "aaid")], "mediumint(8) unsigned")
        self.assertEqual(types[("account", "suspendeduntil")], "datetime")
        self.assertEqual(types[("qs_player_speech", "from")], "varchar(64)")
        self.assertEqual(types[("variables", "ts")], "timestamp")
        self.assertEqual(types[("grid_entries", "heading")], "float")
        self.assertEqual(len([k for k in types if k[0] == "account"]), 28)

    def test_meta(self):
        meta = dict(self.query("SELECT key, value FROM _meta"))
        self.assertEqual(meta["source_version"], "2026-09-27-22_44")
        self.assertEqual(meta["source_files"], "quarm.sql player_tables.sql login_tables.sql data_tables.sql")
        converted = datetime.datetime.fromisoformat(meta["converted_at"])
        self.assertIsNotNone(converted.tzinfo)
        age = datetime.datetime.now(datetime.timezone.utc) - converted
        self.assertLess(abs(age.total_seconds()), 3600)


class MainErrorTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src = Path(tmp.name) / "dump"
        self.src.mkdir()
        self.out = Path(tmp.name) / "quarm.sqlite"
        for name, text in DUMP_FILES.items():
            write_sql(self.src / name, text)

    def test_missing_version_file_is_unknown(self):
        run_main("--src", str(self.src), "--out", str(self.out), "login_tables.sql")
        with contextlib.closing(sqlite3.connect(str(self.out))) as conn:
            meta = dict(conn.execute("SELECT key, value FROM _meta"))
        self.assertEqual(meta["source_version"], "unknown")
        self.assertEqual(meta["source_files"], "login_tables.sql")

    def test_conversion_error_exits_and_leaves_no_output(self):
        bad = E2E_DATA.replace(
            "COLLATE=latin1_swedish_ci;\n" + CS_RESTORE + "\nINSERT INTO `variables`",
            "COLLATE=latin1_swedish_ci CHECKSUM=1;\n" + CS_RESTORE + "\nINSERT INTO `variables`")
        self.assertNotEqual(bad, E2E_DATA)
        write_sql(self.src / "data_tables.sql", bad)
        lineno = bad.split("\n").index(
            ") ENGINE=MyISAM DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci CHECKSUM=1;") + 1
        with self.assertRaises(SystemExit) as ctx:
            run_main("--src", str(self.src), "--out", str(self.out))
        message = str(ctx.exception.code)
        self.assertTrue(message.startswith("Conversion failed: "), message)
        self.assertIn(f"data_tables.sql:{lineno}: unsupported table options", message)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_name(self.out.name + ".tmp").exists())

    def test_missing_dump_file(self):
        (self.src / "login_tables.sql").unlink()
        with self.assertRaises(SystemExit) as ctx:
            run_main("--src", str(self.src), "--out", str(self.out))
        self.assertIn("Missing dump files", str(ctx.exception.code))
        self.assertIn("login_tables.sql", str(ctx.exception.code))
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
