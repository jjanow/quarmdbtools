"""Shared look for the front end's own pages (home page, error pages, simple tool pages)."""
import html

# The same color tokens as the research page, so the pages look like one app.
BASE_CSS = """
:root {
  --bg: #f7f7f5; --panel: #ffffff; --text: #1d1d1b; --muted: #6b6b66; --border: #dcdcd6;
  --accent: #3b5bdb; --warn: #b35c00; --ok: #2b8a3e;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --panel: #1f1f1d; --text: #e8e8e3; --muted: #9a9a94; --border: #34342f;
    --accent: #8ea2ff; --warn: #ffa94d; --ok: #69db7c;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 system-ui, sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 16px; }
h1 { font-size: 20px; margin: 8px 0 4px; }
a { color: var(--accent); }
a.home { text-decoration: none; }
p.muted, .muted { color: var(--muted); }
.warn { color: var(--warn); }
.ok { color: var(--ok); }
code { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 0 4px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-top: 16px; }
.card {
  display: block; background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px 14px; color: var(--text); text-decoration: none;
}
a.card:hover { border-color: var(--accent); }
.card h2 { font-size: 16px; margin: 0 0 4px; color: var(--accent); }
.card p { margin: 0; color: var(--muted); }
.card .cli { margin-top: 8px; font-size: 12px; }
"""

HOME_LINK = '<a class="home" href="/">&larr; Tools</a>'

esc = html.escape


def page(title, body, nav=True):
    """A complete HTML page with the shared style. body is trusted HTML; escape values with esc()."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{BASE_CSS}</style>
</head>
<body>
<main>
{HOME_LINK if nav else ""}
{body}
</main>
</body>
</html>
"""


def error_page(title, message):
    return page(title, f"<h1>{esc(title)}</h1>\n<p class=\"warn\">{esc(message)}</p>")
