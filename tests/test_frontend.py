"""Tests for the ./quarm entry point: tool discovery, command dispatch, the desktop app and the server.

Run from the repo root: python3 -m unittest discover tests
"""
import contextlib
import io
import os
import shutil
import tempfile
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quarmtools import cli, desktop, pages, server, tools  # noqa: E402
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
        self.app = mock.patch.object(desktop, "run", return_value=0).start()
        self.available = mock.patch.object(desktop, "available", return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def test_no_arguments_opens_app(self):
        self.assertEqual(cli.main([]), 0)
        self.app.assert_called_once_with()
        self.serve.assert_not_called()

    def test_no_display_falls_back_to_serve(self):
        self.available.return_value = False
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main([]), 0)
        self.serve.assert_called_once_with()
        self.app.assert_not_called()
        self.assertIn("browser version", err.getvalue())

    def test_serve_options_without_command(self):
        cli.main(["--no-browser", "--port", "9000"])
        self.serve.assert_called_once_with(9000, open_browser=False)

    def test_help_is_not_serve(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.serve.assert_not_called()
        self.assertIn("research", out.getvalue())

    def test_serve_command(self):
        cli.main(["serve"])
        self.serve.assert_called_once_with(None, open_browser=True)
        self.app.assert_not_called()

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


class RouteTest(unittest.TestCase):
    def test_routes(self):
        tool_list = [fake_tool("echo", page=lambda p: f"era={p.get('era')}")]
        self.assertEqual(pages.route("/echo", {"era": "3"}, tool_list), (200, "era=3"))
        self.assertEqual(pages.route("/", {}, tool_list)[0], 200)
        self.assertEqual(pages.route("/nope", {}, tool_list)[0], 404)


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_settings_defaults_and_bad_values(self):
        path = self.tmp / "desktop.json"
        self.assertEqual(desktop.load_settings(path)["theme"], "system")
        path.write_text('{"theme": "neon", "width": "wide", "height": 700, "extra": 1}')
        settings = desktop.load_settings(path)
        self.assertEqual((settings["theme"], settings["width"], settings["height"]), ("system", 1280, 700))
        self.assertNotIn("extra", settings)
        path.write_text("not json")
        self.assertEqual(desktop.load_settings(path)["theme"], "system")

    def test_settings_round_trip(self):
        path = self.tmp / "sub" / "desktop.json"
        settings = desktop.load_settings(path)
        settings["theme"] = "dark"
        desktop.save_settings(settings, path)
        self.assertEqual(desktop.load_settings(path), settings)

    def test_theme(self):
        self.assertEqual(desktop.resolve_theme("system", True), "dark")
        self.assertEqual(desktop.resolve_theme("system", False), "light")
        self.assertEqual(desktop.resolve_theme("light", True), "light")
        self.assertEqual(desktop.resolve_theme("dark", False), "dark")
        self.assertIn('dataset.theme = "dark"', desktop.theme_script("dark"))

    def test_split_uri(self):
        self.assertEqual(desktop.split_uri("quarm://app/"), ("/", {}))
        self.assertEqual(desktop.split_uri("quarm://app/research?era=3"), ("/research", {"era": "3"}))

    def test_launcher_install_and_uninstall(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp)}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["app", "--install"]), 0)
            entry = self.tmp / "applications" / f"{desktop.APP_ID}.desktop"
            icon = self.tmp / "icons" / "hicolor" / "scalable" / "apps" / f"{desktop.APP_ID}.svg"
            text = entry.read_text()
            self.assertIn(f'Exec="{Path(__file__).resolve().parent.parent / "quarm"}" app', text)
            self.assertIn(f"StartupWMClass={desktop.APP_ID}", text)
            self.assertTrue(icon.is_file())
            self.assertEqual(cli.main(["app", "--uninstall"]), 0)
            self.assertFalse(entry.exists() or icon.exists())


class ResearchRenderTest(unittest.TestCase):
    def test_placeholders(self):
        data = {"characters": [], "note": "</script>"}
        static = research.render_html(data)
        self.assertNotIn("/*DATA*/null", static)
        self.assertNotIn("/*THEME*/", static)
        self.assertIn(':root[data-theme="dark"]', static)
        self.assertNotIn("<!--NAV--><h1>", static)
        self.assertNotIn('class="home"', static)
        self.assertNotIn('"</script>"', static)
        self.assertIn('class="home"', research.render_html(data, nav=research.web.HOME_LINK))


if __name__ == "__main__":
    unittest.main()
