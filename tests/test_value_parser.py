"""Tests for the value parser in scripts/mariadb_to_sqlite.py (parse_rows and unescape).

Run from the repo root: python3 -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mariadb_to_sqlite import ConvertError, parse_rows, unescape  # noqa: E402


def values(literal):
    """Parse a single-row literal like "(1,'a')" and return its values."""
    rows, terminated = parse_rows(literal + ";")
    assert terminated and len(rows) == 1
    return rows[0]


class UnescapeTest(unittest.TestCase):
    def test_plain_string_is_unchanged(self):
        self.assertEqual(unescape("Cloth Cap"), "Cloth Cap")

    def test_backslash_escapes(self):
        cases = {
            r"\0": "\0",
            r"\b": "\b",
            r"\n": "\n",
            r"\r": "\r",
            r"\t": "\t",
            r"\Z": "\x1a",
            "\\\\": "\\",
            r"\'": "'",
            r"\"": '"',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(unescape(raw), expected)

    def test_like_wildcard_escapes_keep_backslash(self):
        # MariaDB keeps the backslash for \% and \_ outside LIKE patterns.
        self.assertEqual(unescape(r"100\%"), "100\\%")
        self.assertEqual(unescape(r"a\_b"), "a\\_b")

    def test_unknown_escape_drops_backslash(self):
        self.assertEqual(unescape(r"\x\q"), "xq")

    def test_doubled_quote(self):
        self.assertEqual(unescape("it''s"), "it's")

    def test_escaped_backslash_before_letter_is_not_an_escape(self):
        self.assertEqual(unescape(r"C:\\new"), "C:\\new")

    def test_mixed(self):
        self.assertEqual(unescape(r"Say \"hi\"\r\nto Bob''s \\dog\0"), "Say \"hi\"\r\nto Bob's \\dog\0")


class ParseValuesTest(unittest.TestCase):
    def test_integers(self):
        self.assertEqual(values("(0,1,-1,4294967295,-9223372036854775808)"),
                         [0, 1, -1, 4294967295, -9223372036854775808])
        self.assertTrue(all(type(v) is int for v in values("(0,-5,42)")))

    def test_floats(self):
        row = values("(1.5,-0.25,1.,2e3,-1.5E-2,1.0)")
        self.assertEqual(row, [1.5, -0.25, 1.0, 2000.0, -0.015, 1.0])
        self.assertTrue(all(type(v) is float for v in row))

    def test_null(self):
        self.assertEqual(values("(NULL,1,NULL)"), [None, 1, None])

    def test_string_null_is_text(self):
        self.assertEqual(values("('NULL')"), ["NULL"])

    def test_empty_string(self):
        self.assertEqual(values("('',1,'')"), ["", 1, ""])

    def test_numeric_string_stays_string(self):
        self.assertEqual(values("('42','1.5')"), ["42", "1.5"])

    def test_string_containing_delimiters(self):
        self.assertEqual(values("('a,b','(x)','end;','),(')"), ["a,b", "(x)", "end;", "),("])

    def test_escaped_quotes_inside_string(self):
        self.assertEqual(values(r"(372,'Blessing of E\'ci',1)"), [372, "Blessing of E'ci", 1])
        self.assertEqual(values("(1,'it''s',2)"), [1, "it's", 2])
        self.assertEqual(values(r"('say \"hi\"')"), ['say "hi"'])

    def test_string_ending_in_escaped_backslash(self):
        self.assertEqual(values(r"('a\\',1)"), ["a\\", 1])

    def test_string_ending_in_escaped_quote(self):
        self.assertEqual(values(r"('\'',1)"), ["'", 1])

    def test_escapes_are_decoded(self):
        self.assertEqual(values(r"('line1\r\nline2','x\0')"), ["line1\r\nline2", "x\0"])

    def test_non_ascii(self):
        self.assertEqual(values("('Ïxì — ☃')"), ["Ïxì — ☃"])

    def test_whitespace_between_values(self):
        self.assertEqual(values("( 1 , 'a' ,NULL )"), [1, "a", None])

    def test_real_dump_row(self):
        row = values(r"(3860,0,'Glyphed Skin Sample - Wrist, Right\0',-1,0.5,NULL,'0000-00-00 00:00:00')")
        self.assertEqual(row, [3860, 0, "Glyphed Skin Sample - Wrist, Right\0", -1, 0.5, None,
                               "0000-00-00 00:00:00"])


class ParseRowsTest(unittest.TestCase):
    def test_terminated_statement(self):
        self.assertEqual(parse_rows("(1,'a');"), ([[1, "a"]], True))

    def test_continued_statement(self):
        # mariadb-dump writes one row per line ending in ',' until the final ');'.
        self.assertEqual(parse_rows("(1,'a'),"), ([[1, "a"]], False))

    def test_multiple_rows_on_one_line(self):
        self.assertEqual(parse_rows("(1,'a'),(2,'b'),(3,NULL);"), ([[1, "a"], [2, "b"], [3, None]], True))

    def test_multiple_rows_continued(self):
        self.assertEqual(parse_rows("(1),(2),"), ([[1], [2]], False))

    def test_start_position(self):
        line = "INSERT INTO `t` VALUES (1,'a'),(2,'b');"
        self.assertEqual(parse_rows(line, line.index("VALUES") + len("VALUES")),
                         ([[1, "a"], [2, "b"]], True))

    def test_header_line_without_rows(self):
        line = "INSERT INTO `t` VALUES"
        self.assertEqual(parse_rows(line, len(line)), ([], False))

    def test_empty_and_blank_text(self):
        self.assertEqual(parse_rows(""), ([], False))
        self.assertEqual(parse_rows("   "), ([], False))

    def test_whitespace_after_semicolon_is_allowed(self):
        self.assertEqual(parse_rows("(1);  "), ([[1]], True))


class ParseErrorsTest(unittest.TestCase):
    def assertRejected(self, text, message):
        with self.assertRaises(ConvertError) as ctx:
            parse_rows(text)
        self.assertIn(message, str(ctx.exception))

    def test_unterminated_string(self):
        self.assertRejected("(1,'unterminated", "unrecognized value")

    def test_string_ending_in_lone_backslash(self):
        self.assertRejected("(1,'oops\\')", "unrecognized value")

    def test_hex_literal(self):
        self.assertRejected("(1,0x1F);", "unrecognized value")

    def test_binary_introducer(self):
        self.assertRejected("(1,_binary 'abc');", "unrecognized value")

    def test_bit_literal(self):
        self.assertRejected("(b'1');", "unrecognized value")

    def test_unquoted_word(self):
        self.assertRejected("(1,abc);", "unrecognized value")

    def test_lowercase_null(self):
        self.assertRejected("(null);", "unrecognized value")

    def test_empty_row(self):
        self.assertRejected("();", "unrecognized value")

    def test_missing_separator_between_values(self):
        self.assertRejected("(1 2);", "unrecognized value")

    def test_missing_open_paren(self):
        self.assertRejected("1,2);", "expected '('")

    def test_row_without_trailing_comma_or_semicolon(self):
        self.assertRejected("(1,2)", "row not followed by ',' or ';'")

    def test_text_after_row(self):
        self.assertRejected("(1,2) junk", "unexpected text after row")

    def test_text_after_semicolon(self):
        self.assertRejected("(1,2); (3,4)", "unexpected text after ';'")

    def test_error_reports_column(self):
        with self.assertRaises(ConvertError) as ctx:
            parse_rows("(1,0xFF);")
        self.assertIn("column 4", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
