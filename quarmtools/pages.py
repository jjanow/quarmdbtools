"""The front end's pages: the home page, and routing a path to a tool's page.

Shared by the desktop app (quarmtools/desktop.py) and the web server (quarmtools/server.py), which
only differ in how the HTML gets to the screen.
"""
import traceback

from quarmtools import common, web


def home_page(tool_list):
    db = common.db_info()
    if db["exists"]:
        status = (f'<p class="status"><span class="dot ok"></span>Database dump '
                  f'<b>{web.esc(db["source_version"] or "?")}</b>, converted {web.esc(db["converted_at"] or "?")}. '
                  f'Check for a newer one with <code>./quarm fetch --check</code>.</p>')
    else:
        status = (f'<p class="status"><span class="dot warn"></span>No database at {web.esc(db["path"])}. '
                  f'Run <code>./quarm fetch</code> to download and convert the latest dump.</p>')
    cards = []
    for t in tool_list:
        name, summary = web.esc(t.NAME), web.esc(t.SUMMARY)
        if hasattr(t, "page"):
            cards.append(f'<a class="card" href="/{name}"><h2>{name}</h2><p>{summary}</p>'
                         f'<p class="cli">also <code>./quarm {name}</code></p></a>')
        else:
            cards.append(f'<div class="card cli-only"><h2>{name}</h2><p>{summary}</p>'
                         f'<p class="cli">command line only: <code>./quarm {name}</code></p></div>')
    body = "<h1>Quarm DB Tools</h1>\n" + status + '\n<div class="cards">\n' + "\n".join(cards) + "\n</div>"
    return web.page("Quarm DB Tools", body, nav=False)


def route(path, params, tool_list):
    """(status, html) for a front-end path like "/research" with its query params as {name: value}.

    A tool page that raises gets a 500 error page with the message, rather than no page at all.
    """
    name = path.strip("/")
    pages = {t.NAME: t for t in tool_list if hasattr(t, "page")}
    if name == "":
        return 200, home_page(tool_list)
    if name in pages:
        try:
            return 200, pages[name].page(params)
        except Exception as e:
            traceback.print_exc()
            return 500, web.error_page(name, f"{type(e).__name__}: {e}")
    return 404, web.error_page("Not found", f"no tool page at /{name}")
