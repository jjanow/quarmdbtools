#!/usr/bin/env python3
"""Convert the extracted Project Quarm MariaDB dump into a single SQLite database.

Usage: scripts/mariadb_to_sqlite.py --src DIR [--out FILE] [FILE ...]
       scripts/mariadb_to_sqlite.py --fts-only FILE

Normally run by fetch_latest_db.sh, which extracts the dump to a temp dir, calls this,
and deletes the dump. Reads the .sql files in DIR and writes data/quarm.sqlite. Values are parsed and bound as parameters, never rewritten as SQL text.

Anything the converter does not explicitly understand (new statement kinds, column
options, value literals) is a hard error rather than being silently skipped.

Conversion choices:
  * Column types map to SQLite affinities (INTEGER/REAL/NUMERIC/TEXT/BLOB). The original
    MariaDB types (e.g. "mediumint(8) unsigned") are recorded in the _column_types table.
  * Text columns get COLLATE NOCASE when the MariaDB collation is case-insensitive (*_ci),
    so `WHERE name = 'cloth cap'` behaves as it does in MariaDB.
  * MariaDB zero dates ('0000-00-00 00:00:00') are kept verbatim as text.
  * ON UPDATE CURRENT_TIMESTAMP has no SQLite equivalent and is dropped.
  * Secondary keys become standalone indexes named "<table>__<key>". FULLTEXT keys become
    plain indexes (SQLite full-text search would need a separate FTS5 table).
  * Name search: FTS5 tables items_fts, npc_types_fts and spells_new_fts (see build_fts)
    are built after the load. --fts-only rebuilds them in an existing database.
"""
import argparse
import datetime
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "quarm.sqlite"
DEFAULT_FILES = ["quarm.sql", "player_tables.sql", "login_tables.sql", "data_tables.sql"]
BATCH_SIZE = 5000

TYPE_AFFINITY = {
    **dict.fromkeys(["tinyint", "smallint", "mediumint", "int", "integer", "bigint", "bit", "bool", "boolean"], "INTEGER"),
    **dict.fromkeys(["float", "double", "real"], "REAL"),
    **dict.fromkeys(["decimal", "numeric"], "NUMERIC"),
    **dict.fromkeys(["char", "varchar", "tinytext", "text", "mediumtext", "longtext"], "TEXT"),
    **dict.fromkeys(["date", "datetime", "timestamp", "time", "year"], "TEXT"),
    **dict.fromkeys(["binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob"], "BLOB"),
}
TEXT_TYPES = {"char", "varchar", "tinytext", "text", "mediumtext", "longtext"}

# Name-search FTS5 tables: source table -> FTS table. Each FTS table is a plain (self-contained)
# FTS5 table with columns (name, id UNINDEXED) and rowid = id. External content isn't used
# because items.id is not a rowid alias (items has no PRIMARY KEY, only a unique index).
# unicode61 splits on '_' and punctuation, so 'a_gnoll_pup' and '#Shaman_Ren`Rex' tokenize
# into words. prefix='2 3' speeds up short prefix queries such as 'flam*'.
FTS_TABLES = {"items": "items_fts", "npc_types": "npc_types_fts", "spells_new": "spells_new_fts"}
FTS_OPTIONS = "tokenize = \"unicode61 remove_diacritics 2 separators '_'\", prefix = '2 3'"

# A single-quoted MariaDB string literal (backslash escapes and doubled quotes), unrolled for speed.
STRING_RE = r"'([^'\\]*(?:(?:\\.|'')[^'\\]*)*)'"
VALUE_RE = re.compile(
    r"\s*(?:" + STRING_RE + r"|(-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)|(NULL))\s*([,)])"
)
ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "%": "\\%", "_": "\\_"}
ESCAPE_RE = re.compile(r"\\(.)|''", re.S)

COLUMN_RE = re.compile(r"`(?P<name>[^`]+)` (?P<type>[a-z]+)(?:\((?P<args>[^)]*)\))?(?P<rest>.*)")
COLUMN_OPTION_RE = re.compile(
    r"\s*(?:"
    r"(?P<unsigned>unsigned|zerofill)"
    r"|(?P<notnull>NOT NULL)"
    r"|(?P<null>NULL)"
    r"|DEFAULT (?P<default>" + STRING_RE.replace("(", "(?:", 1) + r"|-?\d+(?:\.\d+)?|NULL|current_timestamp\(\))"
    r"|(?P<autoinc>AUTO_INCREMENT)"
    r"|(?P<onupdate>ON UPDATE current_timestamp\(\))"
    r"|CHARACTER SET (?P<charset>\w+)"
    r"|COLLATE (?P<collate>\w+)"
    r"|COMMENT (?P<comment>" + STRING_RE.replace("(", "(?:", 1) + r")"
    r")"
)
KEY_RE = re.compile(r"(?P<kind>PRIMARY KEY|UNIQUE KEY|FULLTEXT KEY|KEY) (?:`(?P<name>[^`]+)` )?\((?P<cols>[^)]*(?:\(\d+\)[^)]*)*)\)(?: USING (?:BTREE|HASH))?")
KEY_COL_RE = re.compile(r"`([^`]+)`(?:\(\d+\))?")
TABLE_END_RE = re.compile(r"\) ENGINE=\w+(?: AUTO_INCREMENT=\d+)?(?: DEFAULT CHARSET=(?P<charset>\w+))?(?: COLLATE=(?P<collate>\w+))?(?: (?:ROW_FORMAT|PACK_KEYS)=\w+)*(?: COMMENT='(?:[^']|'')*')?;")


class ConvertError(Exception):
    pass


def unescape(s):
    if "\\" not in s and "''" not in s:
        return s
    return ESCAPE_RE.sub(lambda m: "'" if m.group(1) is None else ESCAPES.get(m.group(1), m.group(1)), s)


def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


def parse_rows(text, pos=0):
    """Parse `(v, ...)[,(v, ...)]...` from text starting at pos.

    Returns (rows, terminated) where terminated is True when the statement's `;` was seen.
    """
    rows = []
    n = len(text)
    while True:
        while pos < n and text[pos] in " \t":
            pos += 1
        if pos >= n:
            return rows, False
        if text[pos] != "(":
            raise ConvertError(f"expected '(' at column {pos + 1}")
        pos += 1
        row = []
        while True:
            m = VALUE_RE.match(text, pos)
            if not m:
                raise ConvertError(f"unrecognized value at column {pos + 1}: {text[pos:pos + 40]!r}")
            s, num, null, sep = m.groups()
            if s is not None:
                row.append(unescape(s))
            elif num is not None:
                row.append(float(num) if any(c in num for c in ".eE") else int(num))
            else:
                row.append(None)
            pos = m.end()
            if sep == ")":
                break
        rows.append(row)
        rest = text[pos:pos + 1]
        if rest == ",":
            pos += 1
        elif rest == ";":
            if text[pos + 1:].strip():
                raise ConvertError("unexpected text after ';'")
            return rows, True
        elif text[pos:].strip() == "":
            raise ConvertError("row not followed by ',' or ';'")
        else:
            raise ConvertError(f"unexpected text after row at column {pos + 1}")


class Table:
    def __init__(self, name):
        self.name = name
        self.columns = []        # (name, sqlite_decl)
        self.mariadb_types = []  # (name, original type text)
        self.text_columns = []   # (index in columns, explicit collation or None)
        self.primary_key = None
        self.indexes = []        # (unique, key name, [cols])

    def add_line(self, line):
        line = line.strip().rstrip(",")
        if line.startswith("`"):
            self._add_column(line)
        else:
            m = KEY_RE.fullmatch(line)
            if not m:
                raise ConvertError(f"unsupported table definition line: {line!r}")
            cols = KEY_COL_RE.findall(m.group("cols"))
            if m.group("kind") == "PRIMARY KEY":
                self.primary_key = cols
            else:
                self.indexes.append((m.group("kind") == "UNIQUE KEY", m.group("name"), cols))

    def _add_column(self, line):
        m = COLUMN_RE.fullmatch(line)
        if not m:
            raise ConvertError(f"unsupported column definition: {line!r}")
        name, mtype, rest = m.group("name"), m.group("type").lower(), m.group("rest")
        if mtype not in TYPE_AFFINITY:
            raise ConvertError(f"unsupported column type {mtype!r} in {line!r}")
        parts = [quote_ident(name), TYPE_AFFINITY[mtype]]
        collate = None
        modifiers = []  # unsigned/zerofill, kept for _column_types
        pos = 0
        while pos < len(rest):
            o = COLUMN_OPTION_RE.match(rest, pos)
            if not o or o.end() == pos:
                raise ConvertError(f"unsupported column option {rest[pos:].strip()!r} in {line!r}")
            pos = o.end()
            if o.group("notnull"):
                parts.append("NOT NULL")
            elif o.group("default") is not None:
                d = o.group("default")
                if d == "current_timestamp()":
                    parts.append("DEFAULT CURRENT_TIMESTAMP")
                elif d == "NULL":
                    parts.append("DEFAULT NULL")
                elif d.startswith("'"):
                    parts.append("DEFAULT " + sql_literal(unescape(d[1:-1])))
                else:
                    parts.append("DEFAULT " + d)
            elif o.group("collate"):
                collate = o.group("collate")
            elif o.group("unsigned"):
                modifiers.append(o.group("unsigned"))
            # NULL/AUTO_INCREMENT/ON UPDATE/CHARACTER SET/COMMENT: no SQLite equivalent needed
        if mtype in TEXT_TYPES:
            self.text_columns.append((len(self.columns), collate))
        self.columns.append([name, parts])
        type_text = mtype + (f"({m.group('args')})" if m.group("args") is not None else "")
        type_text = " ".join([type_text] + modifiers)
        self.mariadb_types.append((name, type_text))

    def finish(self, end_line):
        m = TABLE_END_RE.fullmatch(end_line.strip())
        if not m:
            raise ConvertError(f"unsupported table options: {end_line.strip()!r}")
        table_collate = m.group("collate") or ""
        for idx, col_collate in self.text_columns:
            if (col_collate or table_collate).endswith("_ci"):
                self.columns[idx][1].append("COLLATE NOCASE")

    def create_sql(self):
        defs = [" ".join(parts) for _, parts in self.columns]
        if self.primary_key:
            defs.append("PRIMARY KEY (" + ", ".join(map(quote_ident, self.primary_key)) + ")")
        return f"CREATE TABLE {quote_ident(self.name)} (\n  " + ",\n  ".join(defs) + "\n)"

    def index_sql(self):
        for unique, key, cols in self.indexes:
            yield (
                f"CREATE {'UNIQUE ' if unique else ''}INDEX {quote_ident(self.name + '__' + key)} "
                f"ON {quote_ident(self.name)} (" + ", ".join(map(quote_ident, cols)) + ")"
            )


class Converter:
    def __init__(self, conn):
        self.conn = conn
        self.tables = {}
        self.row_counts = {}

    def convert_file(self, path):
        table = None       # Table being defined by CREATE TABLE
        insert = None      # (table, insert sql, ncols, pending rows) while inside INSERT
        with open(path, encoding="utf-8", newline="\n") as f:
            for lineno, line in enumerate(f, 1):
                line = line.rstrip("\n")
                try:
                    if insert is not None:
                        insert = self._insert_rows(insert, line, 0)
                    elif table is not None:
                        if line.startswith(")"):
                            table.finish(line)
                            self._create_table(table)
                            table = None
                        else:
                            table.add_line(line)
                    elif not line.strip() or line.startswith("--"):
                        continue
                    elif line.startswith("/*"):
                        if not line.rstrip().rstrip(";").endswith("*/"):
                            raise ConvertError("unterminated or multi-line comment")
                    elif m := re.fullmatch(r"DROP TABLE IF EXISTS `([^`]+)`;", line):
                        self.conn.execute(f"DROP TABLE IF EXISTS {quote_ident(m.group(1))}")
                        self.conn.execute("DELETE FROM _column_types WHERE table_name = ?", (m.group(1),))
                        self.tables.pop(m.group(1), None)
                    elif m := re.fullmatch(r"CREATE TABLE `([^`]+)` \(", line):
                        table = Table(m.group(1))
                    elif m := re.match(r"INSERT INTO `([^`]+)` VALUES", line):
                        name = m.group(1)
                        if name not in self.tables:
                            raise ConvertError(f"INSERT into undefined table {name!r}")
                        ncols = len(self.tables[name].columns)
                        sql = f"INSERT INTO {quote_ident(name)} VALUES ({', '.join('?' * ncols)})"
                        insert = self._insert_rows((name, sql, ncols, []), line, m.end())
                    elif re.fullmatch(r"(LOCK TABLES `[^`]+` WRITE|UNLOCK TABLES);", line):
                        continue
                    else:
                        raise ConvertError("unsupported statement")
                except (ConvertError, sqlite3.Error, OverflowError) as e:
                    raise ConvertError(f"{path.name}:{lineno}: {e}\n  {line[:200]}") from e
        if table is not None or insert is not None:
            raise ConvertError(f"{path.name}: unexpected end of file inside a statement")

    def _create_table(self, table):
        self.conn.execute(table.create_sql())
        self.conn.executemany(
            "INSERT INTO _column_types VALUES (?, ?, ?)",
            [(table.name, col, t) for col, t in table.mariadb_types],
        )
        self.tables[table.name] = table
        self.row_counts.setdefault(table.name, 0)

    def _insert_rows(self, insert, line, pos):
        name, sql, ncols, pending = insert
        rows, terminated = parse_rows(line, pos)
        for row in rows:
            if len(row) != ncols:
                raise ConvertError(f"row has {len(row)} values, table {name!r} has {ncols} columns")
        pending.extend(rows)
        if terminated or len(pending) >= BATCH_SIZE:
            self.conn.executemany(sql, pending)
            self.row_counts[name] += len(pending)
            pending.clear()
        return None if terminated else insert

    def create_indexes(self):
        for table in self.tables.values():
            for sql in table.index_sql():
                self.conn.execute(sql)


def build_fts(conn):
    """(Re)build the name-search FTS5 tables in conn; returns the FTS table names built.

    Idempotent: each FTS table is dropped and rebuilt from its source table. A missing source
    table is skipped (and any stale FTS table for it dropped). Runs inside a savepoint, so it
    works both within an open transaction and in autocommit mode.
    """
    built = []
    conn.execute("SAVEPOINT build_fts")
    try:
        for source, fts in FTS_TABLES.items():
            conn.execute(f"DROP TABLE IF EXISTS {quote_ident(fts)}")
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (source,)).fetchone():
                continue
            conn.execute(f"CREATE VIRTUAL TABLE {quote_ident(fts)} USING fts5(name, id UNINDEXED, {FTS_OPTIONS})")
            # A few item names end in a NUL byte upstream; index them without it. The explicit
            # rowid also makes a duplicate id fail loudly instead of indexing it twice.
            rows = conn.execute(f"SELECT id, name FROM {quote_ident(source)} WHERE name IS NOT NULL AND name != ''")
            conn.executemany(
                f"INSERT INTO {quote_ident(fts)} (rowid, name, id) VALUES (?, ?, ?)",
                ((i, n.rstrip("\0"), i) for i, n in rows),
            )
            conn.execute(f"INSERT INTO {quote_ident(fts)} ({quote_ident(fts)}) VALUES ('optimize')")
            built.append(fts)
    except BaseException:
        conn.execute("ROLLBACK TO build_fts")
        conn.execute("RELEASE build_fts")
        raise
    conn.execute("RELEASE build_fts")
    return built


def fts_only(path):
    if not path.is_file():
        sys.exit(f"No such database: {path}")
    started = time.monotonic()
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        built = build_fts(conn)
    except sqlite3.Error as e:
        sys.exit(f"Building FTS tables failed: {e}")
    finally:
        conn.close()
    print(f"Built {', '.join(built) or 'no FTS tables'} in {path} in {time.monotonic() - started:.1f}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--src", type=Path, help="directory with the extracted dump")
    mode.add_argument("--fts-only", type=Path, metavar="FILE",
                      help="only (re)build the name-search FTS5 tables in an existing SQLite file, in place")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"SQLite file to write (default: {DEFAULT_OUT})")
    parser.add_argument("files", nargs="*", default=DEFAULT_FILES, help=f"dump files to load, in order (default: {' '.join(DEFAULT_FILES)})")
    args = parser.parse_args()
    if args.fts_only is not None:
        if args.files != DEFAULT_FILES or args.out != DEFAULT_OUT:
            parser.error("--fts-only takes no --out or dump files")
        return fts_only(args.fts_only)

    paths = [args.src / f for f in args.files]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        sys.exit(f"Missing dump files: {', '.join(missing)}")
    version_file = args.src / "VERSION"
    version = version_file.read_text().strip() if version_file.is_file() else "unknown"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    started = time.monotonic()
    conn = sqlite3.connect(tmp, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("BEGIN")
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE _column_types (table_name TEXT, column_name TEXT, mariadb_type TEXT, PRIMARY KEY (table_name, column_name))")
        converter = Converter(conn)
        for path in paths:
            print(f"Loading {path.name} ...", flush=True)
            converter.convert_file(path)
        print("Creating indexes ...", flush=True)
        converter.create_indexes()
        print("Building FTS tables ...", flush=True)
        try:
            build_fts(conn)
        except sqlite3.Error as e:
            raise ConvertError(f"building FTS tables: {e}") from e
        conn.executemany("INSERT INTO _meta VALUES (?, ?)", [
            ("source_version", version),
            ("source_files", " ".join(args.files)),
            ("converted_at", datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")),
        ])
        conn.execute("COMMIT")
        conn.execute("ANALYZE")
    except ConvertError as e:
        conn.close()
        tmp.unlink(missing_ok=True)
        sys.exit(f"Conversion failed: {e}")
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(tmp, args.out)

    total = sum(converter.row_counts.values())
    print(f"Wrote {args.out} (version {version}): {len(converter.tables)} tables, {total:,} rows "
          f"in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
