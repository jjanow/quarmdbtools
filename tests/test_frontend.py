"""Tests for the ./quarm entry point: tool discovery, command dispatch and the front-end server.

Run from the repo root: python3 -m unittest discover tests
"""
import contextlib
import io
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quarmtools import cli, server, tools  # noqa: E402
from quarmtools.tools import research  # noqa: E402


def fake_tool(name, page=None):
    t = types.SimpleNamespace(NAME=name, SUMMARY=f"{name} summary", add_arguments=lambda ap: None,
                              run=mock.Mock(return_value=7))
    if page:
        t.page = page
    return t


class DiscoverTest(unittest.TestCase):
    def test_finds_tools(self):
        names = [t.NAME for t in tools.discover()]
        self.assertEqual(names, sorted(names))
        self.assertIn("research", names)
        self.assertIn("fetch", names)
        self.assertIn("schema-ref", names)

    def test_tools_define_interface(self):
        for t in tools.discover():
            self.assertTrue(t.SUMMARY and callable(t.add_arguments) and callable(t.run), t.NAME)


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.serve = mock.patch.object(server, "serve", return_value=0).start()
        self.addCleanup(mock.patch.stopall)

    def test_no_arguments_serves(self):
        self.assertEqual(cli.main([]), 0)
        self.serve.assert_called_once_with(None, open_browser=True)

    def test_serve_options_without_command(self):
        cli.main(["--no-browser", "--port", "9000"])
        self.serve.assert_called_once_with(9000, open_browser=False)

    def test_help_is_not_serve(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.serve.assert_not_called()
        self.assertIn("research", out.getvalue())

    def test_runs_tool(self):
        tool = fake_tool("demo")
        with mock.patch.object(tools, "discover", return_value=[tool]):
            self.assertEqual(cli.main(["demo"]), 7)
        tool.run.assert_called_once()
        self.serve.assert_not_called()


class ServerTest(unittest.TestCase):
    def setUp(self):
        def broken(params):
            raise RuntimeError("boom")

        self.tools = [fake_tool("cli-only"), fake_tool("echo", page=lambda p: f"<p>era={p.get('era')}</p>"),
                      fake_tool("broken", page=broken)]
        self.server = server.make_server(port=0, tool_list=self.tools)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        quiet = mock.patch("sys.stderr", io.StringIO())
        quiet.start()
        self.addCleanup(quiet.stop)

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8")

    def test_home_lists_tools(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn('href="/echo"', body)
        self.assertIn("command line only: <code>./quarm cli-only</code>", body)
        self.assertNotIn('href="/cli-only"', body)

    def test_tool_page_gets_query(self):
        self.assertEqual(self.get("/echo?era=3"), (200, "<p>era=3</p>"))

    def test_errors(self):
        self.assertEqual(self.get("/cli-only")[0], 404)
        self.assertEqual(self.get("/nope")[0], 404)
        status, body = self.get("/broken")
        self.assertEqual(status, 500)
        self.assertIn("RuntimeError: boom", body)


class ResearchRenderTest(unittest.TestCase):
    def test_placeholders(self):
        data = {"characters": [], "note": "</script>"}
        static = research.render_html(data)
        self.assertNotIn("/*DATA*/null", static)
        self.assertNotIn("<!--NAV--><h1>", static)
        self.assertNotIn('class="home"', static)
        self.assertNotIn('"</script>"', static)
        self.assertIn('class="home"', research.render_html(data, nav=research.web.HOME_LINK))


if __name__ == "__main__":
    unittest.main()
