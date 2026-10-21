"""./quarm: run a tool by name, or start the front end when no tool is given."""
import argparse

from quarmtools import server, tools


def build_parser(tool_list):
    ap = argparse.ArgumentParser(
        prog="quarm", description="Project Quarm database tools. With no command, starts the front end.")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")
    sp = sub.add_parser("serve", help="start the front end in your browser (the default)",
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
    # No command (or only serve's options, e.g. ./quarm --no-browser) means serve.
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["serve"] + list(argv)
    args = build_parser(tool_list).parse_args(argv)
    if args.cmd == "serve":
        return server.serve(args.port, open_browser=not args.no_browser)
    return args.tool.run(args)
