"""Tool modules. Each module in this package is one ./quarm subcommand.

A tool module defines:

- NAME: the subcommand (e.g. "research").
- SUMMARY: one line, shown in ./quarm --help and on the home page.
- add_arguments(parser): its command-line options.
- run(args) -> int: the command-line entry point; returns the exit code.
- page(params) -> str (optional): an HTML page for the front end, served at /<NAME>. params is
  the query string as {name: value}. Tools without one are listed on the home page as CLI only.

Modules whose name starts with "_" are skipped.
"""
import importlib
import pkgutil


def discover():
    """Tool modules, sorted by NAME."""
    tools = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(module, "NAME") and hasattr(module, "run"):
            tools.append(module)
    return sorted(tools, key=lambda m: m.NAME)
