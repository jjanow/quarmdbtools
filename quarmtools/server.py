"""The front end: a local web server with a home page linking to each tool's page.

Each tool with a page(params) function is served at /<NAME>, rebuilt on every request, so reloading
the page picks up a new database or fresh Zeal exports. The server only listens on 127.0.0.1.
"""
import errno
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from quarmtools import common, tools, web

DEFAULT_PORT = 8642


def home_page(tool_list):
    db = common.db_info()
    if db["exists"]:
        status = (f'<p class="muted">Database: dump <b>{web.esc(db["source_version"] or "?")}</b>, '
                  f'converted {web.esc(db["converted_at"] or "?")}. '
                  f'Check for a newer one with <code>./quarm fetch --check</code>.</p>')
    else:
        status = (f'<p class="warn">No database at {web.esc(db["path"])}. '
                  f'Run <code>./quarm fetch</code> to download and convert the latest dump.</p>')
    cards = []
    for t in tool_list:
        name, summary = web.esc(t.NAME), web.esc(t.SUMMARY)
        if hasattr(t, "page"):
            cards.append(f'<a class="card" href="/{name}"><h2>{name}</h2><p>{summary}</p>'
                         f'<p class="cli">also <code>./quarm {name}</code></p></a>')
        else:
            cards.append(f'<div class="card"><h2>{name}</h2><p>{summary}</p>'
                         f'<p class="cli">command line only: <code>./quarm {name}</code></p></div>')
    body = "<h1>Quarm DB Tools</h1>\n" + status + '\n<div class="cards">\n' + "\n".join(cards) + "\n</div>"
    return web.page("Quarm DB Tools", body, nav=False)


def make_handler(tool_list):
    pages = {t.NAME: t for t in tool_list if hasattr(t, "page")}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            name = url.path.strip("/")
            params = dict(urllib.parse.parse_qsl(url.query))
            if name == "":
                self.send_html(200, home_page(tool_list))
            elif name in pages:
                try:
                    self.send_html(200, pages[name].page(params))
                except Exception as e:  # show the error in the browser rather than dropping the connection
                    traceback.print_exc()
                    self.send_html(500, web.error_page(name, f"{type(e).__name__}: {e}"))
            elif name == "favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_html(404, web.error_page("Not found", f"no tool page at /{name}"))

        def send_html(self, status, text):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            sys.stderr.write("  " + (fmt % args) + "\n")

    return Handler


def make_server(port=None, tool_list=None):
    """Bind the server on 127.0.0.1. With no port, use DEFAULT_PORT, or any free port if it's taken."""
    handler = make_handler(tools.discover() if tool_list is None else tool_list)
    try:
        return ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT if port is None else port), handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE or port is not None:
            raise
        return ThreadingHTTPServer(("127.0.0.1", 0), handler)


def serve(port=None, open_browser=True):
    try:
        server = make_server(port)
    except OSError as e:
        print(f"error: can't listen on 127.0.0.1:{DEFAULT_PORT if port is None else port}: {e.strerror}",
              file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Quarm DB Tools at {url} (Ctrl-C to stop)", flush=True)
    if open_browser:
        # In a thread: without a display, webbrowser may pick a console browser that blocks until it exits.
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0
