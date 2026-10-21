"""Shared look for the front end's pages: theme tokens, base style and the page wrapper."""
import html

# Color tokens for every page, the research page included (its template has a /*THEME*/ placeholder).
# Pages follow the system light/dark preference unless <html data-theme="light|dark"> says otherwise;
# the desktop app sets data-theme from its theme menu.
_LIGHT = """
  color-scheme: light;
  --bg: #f5f6f8; --panel: #ffffff; --panel-2: #f0f2f5; --text: #1b1e24; --muted: #667085;
  --border: #e2e5ea; --accent: #4f46e5; --on-accent: #ffffff; --accent-soft: #eef0ff; --row-alt: #f8f9fb; --hover: #f1f3ff;
  --done: #98a2b3; --warn: #c2410c; --ok: #15803d; --dup: #7c3aed;
  --shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 1px 3px rgba(16, 24, 40, .06);
  --shadow-lift: 0 4px 12px rgba(16, 24, 40, .08), 0 2px 4px rgba(16, 24, 40, .05);
"""
_DARK = """
  color-scheme: dark;
  --bg: #0f1115; --panel: #171a21; --panel-2: #1d212a; --text: #e6e8ec; --muted: #8b93a1;
  --border: #272c36; --accent: #8b8cf8; --on-accent: #0f1115; --accent-soft: #23254a; --row-alt: #1a1d25; --hover: #20233a;
  --done: #5d6573; --warn: #fb923c; --ok: #4ade80; --dup: #c084fc;
  --shadow: 0 1px 2px rgba(0, 0, 0, .3);
  --shadow-lift: 0 6px 16px rgba(0, 0, 0, .4);
"""
THEME_CSS = f"""
:root {{{_LIGHT}}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{{_DARK}}}
}}
:root[data-theme="dark"] {{{_DARK}}}
:root {{ --font: "Inter", "Cantarell", "Ubuntu", system-ui, -apple-system, "Segoe UI", sans-serif; --radius: 10px; }}
"""

BASE_CSS = THEME_CSS + """
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 var(--font);
  -webkit-font-smoothing: antialiased;
}
main { max-width: 960px; margin: 0 auto; padding: 28px 24px; }
h1 { font-size: 24px; font-weight: 650; letter-spacing: -0.01em; margin: 8px 0 6px; }
a { color: var(--accent); }
a.home { display: inline-block; text-decoration: none; font-size: 13px; margin-bottom: 4px; }
p.muted, .muted { color: var(--muted); }
.warn { color: var(--warn); }
.ok { color: var(--ok); }
code {
  font: 12px/1.4 ui-monospace, "JetBrains Mono", "Ubuntu Mono", monospace; background: var(--panel-2);
  border: 1px solid var(--border); border-radius: 5px; padding: 1px 5px;
}
.status { color: var(--muted); margin: 0; }
.status b { color: var(--text); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; display: inline-block; margin: 0 8px 1px 0; }
.dot.ok { color: var(--ok); }
.dot.warn { color: var(--warn); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; margin-top: 24px; }
.card {
  display: block; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px 18px; color: var(--text); text-decoration: none; box-shadow: var(--shadow);
  transition: border-color .15s, box-shadow .15s, transform .15s;
}
a.card:hover { border-color: var(--accent); box-shadow: var(--shadow-lift); transform: translateY(-1px); }
.card h2 { font-size: 16px; font-weight: 600; margin: 0 0 6px; color: var(--text); }
a.card h2 { color: var(--accent); }
.card p { margin: 0; color: var(--muted); }
.card.cli-only { background: transparent; box-shadow: none; border-style: dashed; }
.card .cli { margin-top: 12px; font-size: 12px; }
p.warn { background: var(--panel); border: 1px solid var(--border); border-left: 3px solid var(--warn);
  border-radius: var(--radius); padding: 12px 14px; }
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
