#!/usr/bin/env python3
"""Generate a Markdown schema reference (tables, columns, indexes) from the SQLite database.

Usage: scripts/gen_schema_ref.py [--db FILE] [--out FILE]

Reads data/quarm.sqlite (opened read-only) and writes docs/schema_reference.md. Rerun it
after fetch_latest_db.sh converts a new dump; the output is deterministic, so a diff of the
reference shows only real schema or row-count changes.

What it lists, per table: row count; each column's name, original MariaDB type (from
_column_types), SQLite affinity, NOT NULL, default, NOCASE collation and primary-key
position; and the table's indexes. Tables and indexes are sorted by name. Columns and index
columns keep their defined order, which is meaningful.

Left out: the converter's own tables (_meta, _column_types), SQLite's internal sqlite_*
tables, virtual tables (e.g. FTS5 search tables) and their shadow tables.

The database doesn't record which dump file a table came from, so tables are grouped into
those with rows and those that ship empty (mostly the player/login schema).
"""
import argparse
import re
import sqlite3
import sys
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "quarm.sqlite"
DEFAULT_OUT = REPO_ROOT / "docs" / "schema_reference.md"
INTERNAL_TABLES = {"_meta", "_column_types"}
# Shadow-table suffixes of SQLite's virtual-table modules (FTS3/4, FTS5, R*Tree). Only names
# "<virtual table>_<suffix>" are excluded, so real tables like character_data are kept.
SHADOW_SUFFIXES = ["content", "segments", "segdir", "docsize", "stat", "data", "idx", "config", "node", "rowid", "parent"]

STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
# Leading identifier of a definition: "quoted", `quoted`, [quoted] or bare.
IDENT_RE = re.compile(r'"((?:[^"]|"")+)"|`([^`]+)`|\[([^\]]+)\]|(\w+)')
CONSTRAINT_KEYWORDS = {"CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"}


class SchemaError(Exception):
    pass


def quote_ident(name):
    return '"' + name.replace('"', '""') + '"'


def open_readonly(path):
    if not path.is_file():
        raise SchemaError(f"database not found: {path}")
    uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=30)


def documented_tables(conn):
    """Names of the regular tables to document, sorted."""
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    virtual = {name for name, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL TABLE")}
    excluded = set(INTERNAL_TABLES) | virtual
    excluded.update(f"{v}_{suffix}" for v in virtual for suffix in SHADOW_SUFFIXES)
    # SQLite 3.37+ also reports shadow tables directly (older versions ignore the unknown pragma).
    excluded.update(row[1] for row in conn.execute("PRAGMA main.table_list") if row[2] in ("shadow", "virtual"))
    return sorted(
        name for name, _ in rows
        if name not in excluded and not name.startswith("sqlite_")
    )


def split_definitions(create_sql):
    """Split the body of a CREATE TABLE statement into its column and constraint definitions."""
    parts, depth, buf, quote = [], 0, [], None
    for ch in create_sql:
        if quote or ch in "'\"`[":
            # Inside a quoted string or identifier; a doubled quote closes and reopens it.
            if quote is None:
                quote = "]" if ch == "[" else ch
            elif ch == quote:
                quote = None
            if depth >= 1:
                buf.append(ch)
        elif ch == "(":
            depth += 1
            if depth > 1:
                buf.append(ch)
        elif ch == ")":
            depth -= 1
            if depth >= 1:
                buf.append(ch)
        elif ch == "," and depth == 1:
            parts.append("".join(buf).strip())
            buf = []
        elif depth >= 1:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return parts


def nocase_columns(create_sql, table, column_names):
    """Names of the columns declared COLLATE NOCASE in the table's CREATE TABLE statement."""
    result, seen = set(), []
    for part in split_definitions(create_sql):
        m = IDENT_RE.match(part)
        if not m or (m.group(4) or "").upper() in CONSTRAINT_KEYWORDS:
            continue
        name = next(g for g in m.groups() if g is not None)
        name = name.replace('""', '"') if m.group(1) is not None else name
        seen.append(name)
        rest = STRING_LITERAL_RE.sub("''", part[m.end():]).upper()
        collations = re.findall(r"\bCOLLATE\s+(\w+)", rest)
        if collations == ["NOCASE"]:
            result.add(name)
        elif collations:
            raise SchemaError(f"{table}: unexpected collation in column definition {part!r}")
    if seen != column_names:
        raise SchemaError(f"{table}: could not parse column definitions from CREATE TABLE")
    return result


def describe_table(conn, name, mariadb_types):
    create_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()[0]
    info = conn.execute(f"PRAGMA table_info({quote_ident(name)})").fetchall()
    nocase = nocase_columns(create_sql, name, [r[1] for r in info])
    columns = []
    for cid, col, decl, notnull, default, pk in info:
        columns.append({
            "name": col,
            "mariadb": mariadb_types.get((name, col)),
            "affinity": decl,
            "notnull": bool(notnull),
            "default": default,
            "nocase": col in nocase,
            "pk": pk,
        })
    indexes = []
    for _, index, unique, origin, _partial in conn.execute(f"PRAGMA index_list({quote_ident(name)})"):
        if origin == "pk":
            continue  # shown as the PK column
        cols = [r[2] for r in sorted(conn.execute(f"PRAGMA index_info({quote_ident(index)})"))]
        indexes.append((index, bool(unique), cols))
    indexes.sort()
    rows = conn.execute(f"SELECT count(*) FROM {quote_ident(name)}").fetchone()[0]
    return {"name": name, "rows": rows, "columns": columns, "indexes": indexes}


def count(n, noun):
    return f"{n:,} {noun}" + ("" if n == 1 else "s")


def slug(heading):
    """GitHub's heading anchor for a heading text."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def cell(text):
    """Render text as an inline-code Markdown table cell."""
    text = text.replace("|", "\\|").replace("\n", "\\n").replace("\r", "\\r")
    fence = "``" if "`" in text else "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def render(meta, tables):
    groups = [
        (
            "Tables with rows",
            "Game content from `quarm.sql` plus server configuration from `data_tables.sql` "
            "(`rule_values`, `variables`, `command_settings`, ...).",
            [t for t in tables if t["rows"]],
        ),
        (
            "Empty tables (schema only)",
            "Tables that ship with no rows: mostly the player/character schema "
            "(`player_tables.sql`) and login server schema (`login_tables.sql`), plus some "
            "content and server tables that are empty in the dump.",
            [t for t in tables if not t["rows"]],
        ),
    ]
    anchors = {}
    for heading in ["Contents"] + [g[0] for g in groups] + [t["name"] for t in tables]:
        a = slug(heading)
        if a in anchors:
            raise SchemaError(f"headings {anchors[a]!r} and {heading!r} share anchor #{a}")
        anchors[a] = heading

    out = [
        "# Schema reference",
        "",
        "<!-- Generated by scripts/gen_schema_ref.py from data/quarm.sqlite. Do not edit by hand; rerun the script. -->",
        "",
        "Generated by `scripts/gen_schema_ref.py` from `data/quarm.sqlite`. Do not edit by hand.",
        "",
        f"- Source dump: `{meta.get('source_version', 'unknown')}`",
        f"- {count(len(tables), 'table')}, {count(sum(t['rows'] for t in tables), 'row')}",
        "",
        "Column flags: **NN** = NOT NULL, **NOCASE** = `COLLATE NOCASE` (case-insensitive in MariaDB), "
        "**PK** = position in the primary key. An empty Default means none; `NULL` means `DEFAULT NULL`. "
        "Indexes are named `<table>__<MariaDB key name>`.",
        "",
        "## Contents",
        "",
    ]
    for title, _, members in groups:
        out.append(f"- [{title}](#{slug(title)}) ({count(len(members), 'table')})")
        for t in members:
            out.append(f"  - [{t['name']}](#{slug(t['name'])}) ({count(t['rows'], 'row')})")
    for title, blurb, members in groups:
        out += ["", f"## {title}", "", blurb]
        for t in members:
            out += ["", f"### {t['name']}", "", count(t["rows"], "row"), ""]
            out.append("| Column | MariaDB type | Affinity | NN | Default | NOCASE | PK |")
            out.append("|---|---|---|---|---|---|---|")
            for c in t["columns"]:
                out.append("| " + " | ".join([
                    cell(c["name"]),
                    cell(c["mariadb"]) if c["mariadb"] is not None else "?",
                    c["affinity"],
                    "yes" if c["notnull"] else "",
                    cell(c["default"]) if c["default"] is not None else "",
                    "yes" if c["nocase"] else "",
                    str(c["pk"]) if c["pk"] else "",
                ]) + " |")
            if t["indexes"]:
                out += ["", "Indexes:", ""]
                for index, unique, cols in t["indexes"]:
                    kind = "unique" if unique else "index"
                    out.append(f"- {cell(index)} ({kind}): " + ", ".join(cell(c) for c in cols))
    out.append("")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite database to read (default: {DEFAULT_DB})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Markdown file to write (default: {DEFAULT_OUT})")
    args = parser.parse_args()

    try:
        conn = open_readonly(args.db)
        try:
            meta = dict(conn.execute("SELECT key, value FROM _meta"))
            mariadb_types = {(t, c): mt for t, c, mt in conn.execute("SELECT table_name, column_name, mariadb_type FROM _column_types")}
            tables = [describe_table(conn, name, mariadb_types) for name in documented_tables(conn)]
        finally:
            conn.close()
        text = render(meta, tables)
    except (SchemaError, sqlite3.Error) as e:
        sys.exit(f"Schema reference failed: {e}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"Wrote {args.out}: {len(tables)} tables")


if __name__ == "__main__":
    main()
