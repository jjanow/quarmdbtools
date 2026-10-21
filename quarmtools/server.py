"""The front end in a browser: a local web server for the pages in quarmtools/pages.py.

The desktop app (quarmtools/desktop.py) is the default; this is for running without a display or in
a real browser. Pages are rebuilt on every request, so reloading picks up a new database or fresh
Zeal exports. The server only listens on 127.0.0.1.
"""
import errno
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from quarmtools import pages, tools

DEFAULT_PORT = 8642


def make_handler(tool_list):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            self.send_html(*pages.route(url.path, dict(urllib.parse.parse_qsl(url.query)), tool_list))

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
