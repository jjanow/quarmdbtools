"""./quarm: run a tool by name, or open the front end when no tool is given."""
import argparse
import sys

from quarmtools import desktop, server, tools


def build_parser(tool_list):
    ap = argparse.ArgumentParser(
        prog="quarm", description="Project Quarm database tools. With no command, opens the desktop app.")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")
    app = sub.add_parser("app", help="open the desktop app (the default)",
                         description="Open the front end in its own window.")
    launcher = app.add_mutually_exclusive_group()
    launcher.add_argument("--install", action="store_true", help="add the app to your application menu")
    launcher.add_argument("--uninstall", action="store_true", help="remove it from your application menu")
    sp = sub.add_parser("serve", help="start the front end in your browser instead",
                        description="Start the front end on 127.0.0.1 and open it in your browser.")
    sp.add_argument("--port", type=int,
                    help=f"port to listen on (default: {server.DEFAULT_PORT}, or any free port if that one is taken)")
    sp.add_argument("--no-browser", action="store_true", help="don't open a browser")
    for t in tool_list:
        tp = sub.add_parser(t.NAME, help=t.SUMMARY, description=t.SUMMARY)
        t.add_arguments(tp)
        tp.set_defaults(tool=t)
    return ap


def main(argv):
    tool_list = tools.discover()
    if not argv:
        argv = ["app"]
    # Only serve's options (e.g. ./quarm --no-browser) means serve.
    elif argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["serve"] + list(argv)
    args = build_parser(tool_list).parse_args(argv)
    if args.cmd == "app":
        if args.install:
            return desktop.install_launcher()
        if args.uninstall:
            return desktop.uninstall_launcher()
        if desktop.available():
            return desktop.run()
        print("No display, or GTK/WebKitGTK bindings missing (gir1.2-webkit2-4.1); "
              "starting the browser version instead.", file=sys.stderr)
        return server.serve()
    if args.cmd == "serve":
        return server.serve(args.port, open_browser=not args.no_browser)
    return args.tool.run(args)
