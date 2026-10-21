"""The front end as a desktop app: a GTK window showing the pages from quarmtools/pages.py.

Pages are served to an embedded WebKit view through a private quarm:// URI scheme, built in this
process by the same code the web server uses; nothing listens on a port. Links to other sites open
in the default browser, downloads ask where to save, and the theme menu picks light, dark or the
system setting.

Needs GTK 3 and WebKitGTK (WebKit2 4.1) with their GObject introspection bindings (on Debian and
Ubuntu: gir1.2-gtk-3.0 and gir1.2-webkit2-4.1). The GTK modules are only imported when the app
starts, so the rest of ./quarm works without them.
"""
import json
import os
import sys
import threading
import urllib.parse
from pathlib import Path

from quarmtools import common, pages, tools

APP_ID = "io.github.jjanow.QuarmDBTools"
APP_NAME = "Quarm DB Tools"
SCHEME = "quarm"
HOME_URI = f"{SCHEME}://app/"
ICON = Path(__file__).resolve().parent / "assets" / "quarmdbtools.svg"
THEMES = ("system", "light", "dark")
# Dark page background, painted before a page loads so switching pages doesn't flash white.
DARK_BG, LIGHT_BG = "#0f1115", "#f5f6f8"


def available():
    """True if there's a display and the GTK and WebKit bindings load."""
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------- settings

def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "quarmdbtools" / "desktop.json"


def load_settings(path=None):
    """{"theme", "width", "height", "maximized"}, with defaults for anything missing or unreadable."""
    settings = {"theme": "system", "width": 1280, "height": 860, "maximized": False}
    try:
        saved = json.loads((path or config_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        settings.update({k: v for k, v in saved.items() if k in settings and type(v) is type(settings[k])})
    if settings["theme"] not in THEMES:
        settings["theme"] = "system"
    return settings


def save_settings(settings, path=None):
    path = path or config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"warning: can't save settings to {path}: {e}", file=sys.stderr)


def resolve_theme(theme, system_dark):
    """"light" or "dark" for a theme setting, given whether the system prefers dark."""
    if theme == "system":
        return "dark" if system_dark else "light"
    return theme


def theme_script(mode):
    """JavaScript that puts the theme on the page's <html data-theme>, which the page CSS follows."""
    return f'document.documentElement.dataset.theme = "{mode}";'


def split_uri(uri):
    """(path, params) for a quarm:// URI, as pages.route takes them."""
    url = urllib.parse.urlsplit(uri)
    return url.path or "/", dict(urllib.parse.parse_qsl(url.query))


# ---------------------------------------------------------------------------- launcher

def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_entry(command):
    """The .desktop launcher text. command is the absolute path to ./quarm."""
    return f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Project Quarm database tools
Exec="{command}" app
Icon={APP_ID}
Terminal=false
Categories=Game;
StartupWMClass={APP_ID}
StartupNotify=true
"""


def install_launcher():
    """Add the app to the desktop's application menu (for this user)."""
    apps = data_home() / "applications"
    icons = data_home() / "icons" / "hicolor" / "scalable" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    entry = apps / f"{APP_ID}.desktop"
    entry.write_text(desktop_entry(common.REPO_ROOT / "quarm"), encoding="utf-8")
    (icons / f"{APP_ID}.svg").write_bytes(ICON.read_bytes())
    print(f"installed {entry}\n{APP_NAME} is now in your application menu. "
          f"Run ./quarm app --uninstall to remove it. If you move this repo, run --install again.")
    return 0


def uninstall_launcher():
    for path in (data_home() / "applications" / f"{APP_ID}.desktop",
                 data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"):
        if path.exists():
            path.unlink()
            print(f"removed {path}")
    return 0


# ---------------------------------------------------------------------------- the app

def run():
    """Open the app window and run until it's closed. Returns the exit code."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2

    tool_list = tools.discover()
    settings = load_settings()

    class state:
        theme_callbacks = []  # one per window, called when the theme setting or the system's changes
        server = None  # the web server, once "Open in Browser" has started it

    # Where WebKit keeps the pages' localStorage (the research page remembers its filters there).
    data_dir = Path(GLib.get_user_data_dir()) / "quarmdbtools" / "webkit"
    cache_dir = Path(GLib.get_user_cache_dir()) / "quarmdbtools" / "webkit"
    data_manager = WebKit2.WebsiteDataManager(base_data_directory=str(data_dir),
                                              base_cache_directory=str(cache_dir))
    context = WebKit2.WebContext.new_with_website_data_manager(data_manager)
    context.get_security_manager().register_uri_scheme_as_secure(SCHEME)

    def finish(request, status, html):
        body = html.encode("utf-8")
        response = WebKit2.URISchemeResponse.new(Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(body)),
                                                 len(body))
        response.set_status(status, None)
        response.set_content_type("text/html; charset=utf-8")
        request.finish_with_response(response)
        return False

    def serve_page(request):
        # Pages can take a few seconds (reading every character's exports), so build them off the
        # main thread and hand the result back to it; WebKit objects are only touched on the main thread.
        path, params = split_uri(request.get_uri())

        def work():
            try:
                status, html = pages.route(path, params, tool_list)
            except Exception as e:  # route catches tool errors; this is only a safety net
                status, html = 500, f"<pre>{type(e).__name__}: {e}</pre>"
            GLib.idle_add(finish, request, status, html)
        threading.Thread(target=work, daemon=True).start()

    context.register_uri_scheme(SCHEME, serve_page)

    app = Gtk.Application(application_id=APP_ID)
    GLib.set_application_name(APP_NAME)
    Gtk.Window.set_default_icon_name(APP_ID)

    def on_activate(app):
        if app.get_windows():  # ./quarm run again: bring the open window forward
            app.get_windows()[0].present()
            return
        build_window(app)

    def build_window(app):
        win = Gtk.ApplicationWindow(application=app, title=APP_NAME)
        win.set_default_size(settings["width"], settings["height"])
        if settings["maximized"]:
            win.maximize()
        if not Gtk.IconTheme.get_default().has_icon(APP_ID):  # launcher not installed
            win.set_icon_from_file(str(ICON))

        content = WebKit2.UserContentManager()
        view = WebKit2.WebView(web_context=context, user_content_manager=content)
        view_settings = view.get_settings()
        view_settings.set_enable_developer_extras(bool(os.environ.get("QUARM_DEVTOOLS")))
        view_settings.set_enable_back_forward_navigation_gestures(True)
        # The header bar has a home button, so the pages' own "← Tools" link is redundant here.
        content.add_style_sheet(WebKit2.UserStyleSheet.new(
            "a.home { display: none !important; }", WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserStyleLevel.USER, None, None))

        # ---- header bar
        header = Gtk.HeaderBar(show_close_button=True, title=APP_NAME)
        win.set_titlebar(header)

        def button(icon, tooltip, action):
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.set_tooltip_text(tooltip)
            b.set_action_name(action)
            return b

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        nav.get_style_context().add_class("linked")
        nav.pack_start(button("go-previous-symbolic", "Back (Alt+Left)", "win.back"), False, False, 0)
        nav.pack_start(button("go-next-symbolic", "Forward (Alt+Right)", "win.forward"), False, False, 0)
        header.pack_start(nav)
        header.pack_start(button("go-home-symbolic", "Tools (Alt+Home)", "win.home"))

        menu = Gio.Menu()
        themes = Gio.Menu()
        for theme in THEMES:
            themes.append(theme.capitalize(), f"app.theme::{theme}")
        menu.append_section("Appearance", themes)
        other = Gio.Menu()
        other.append("Open in Browser", "win.browser")
        other.append("Quit", "app.quit")
        menu.append_section(None, other)
        menu_button = Gtk.MenuButton(menu_model=menu, tooltip_text="Menu")
        menu_button.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        header.pack_end(menu_button)
        header.pack_end(button("view-refresh-symbolic", "Reload (Ctrl+R)", "win.reload"))
        spinner = Gtk.Spinner()
        header.pack_end(spinner)

        # ---- actions
        def action(name, callback, target=win):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: callback())
            target.add_action(a)
            return a

        back = action("back", view.go_back)
        forward = action("forward", view.go_forward)
        action("home", lambda: view.load_uri(HOME_URI))
        action("reload", view.reload)
        action("browser", lambda: open_in_browser(view))
        for name, accels in {"win.back": ["<Alt>Left"], "win.forward": ["<Alt>Right"],
                             "win.home": ["<Alt>Home"], "win.reload": ["<Control>r", "F5"],
                             "app.quit": ["<Control>q"]}.items():
            app.set_accels_for_action(name, accels)

        def update_nav(*_):
            back.set_enabled(view.can_go_back())
            forward.set_enabled(view.can_go_forward())

        def on_load_changed(view, event):
            update_nav()
            if event == WebKit2.LoadEvent.FINISHED:
                spinner.stop()
            else:
                spinner.start()

        view.connect("load-changed", on_load_changed)
        view.connect("notify::title", lambda *_: header.set_title(view.get_title() or APP_NAME))

        # ---- links: our pages stay in the window, everything else goes to the default browser
        def on_decide_policy(view, decision, kind):
            if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                            WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
                return False
            uri = decision.get_navigation_action().get_request().get_uri()
            scheme = urllib.parse.urlsplit(uri).scheme
            if scheme in (SCHEME, "blob", "data", "about") and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                return False
            decision.ignore()
            if scheme == SCHEME:  # target=_blank to one of our pages: open it here
                view.load_uri(uri)
            elif scheme in ("http", "https", "mailto"):
                Gtk.show_uri_on_window(win, uri, Gdk.CURRENT_TIME)
            return True

        view.connect("decide-policy", on_decide_policy)

        # ---- theme
        def apply_theme():
            mode = resolve_theme(settings["theme"], system_prefers_dark())
            Gtk.Settings.get_default().props.gtk_application_prefer_dark_theme = mode == "dark"
            bg = Gdk.RGBA()
            bg.parse(DARK_BG if mode == "dark" else LIGHT_BG)
            view.set_background_color(bg)
            content.remove_all_scripts()
            content.add_script(WebKit2.UserScript.new(
                theme_script(mode), WebKit2.UserContentInjectedFrames.ALL_FRAMES,
                WebKit2.UserScriptInjectionTime.START, None, None))
            view.evaluate_javascript(theme_script(mode), -1, None, None, None, None)

        state.theme_callbacks.append(apply_theme)
        apply_theme()

        # ---- window size, remembered between runs
        def on_delete(*_):
            settings["maximized"] = win.is_maximized()
            if not settings["maximized"]:
                settings["width"], settings["height"] = win.get_size()
            save_settings(settings)
            return False

        win.connect("delete-event", on_delete)
        win.add(view)
        view.load_uri(HOME_URI)
        win.show_all()

    def open_in_browser(view):
        # The pages only exist in this process, so start the web server version and open that.
        from quarmtools import server
        if state.server is None:
            state.server = server.make_server(tool_list=tool_list)
            threading.Thread(target=state.server.serve_forever, daemon=True).start()
        path = urllib.parse.urlsplit(view.get_uri() or HOME_URI)
        port = state.server.server_address[1]
        uri = f"http://127.0.0.1:{port}{path.path or '/'}" + (f"?{path.query}" if path.query else "")
        Gtk.show_uri_on_window(view.get_toplevel(), uri, Gdk.CURRENT_TIME)

    # ---- downloads (the research page's CSV button): ask where to save
    def on_download(context, download):
        def decide(download, suggested):
            dialog = Gtk.FileChooserNative.new("Save File", app.get_active_window(), Gtk.FileChooserAction.SAVE,
                                               "_Save", "_Cancel")
            dialog.set_do_overwrite_confirmation(True)
            dialog.set_current_name(suggested)
            downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            if downloads:
                dialog.set_current_folder(downloads)
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                download.set_allow_overwrite(True)
                download.set_destination(dialog.get_uri())
            else:
                download.cancel()
            return True
        download.connect("decide-destination", decide)
        download.connect("failed", lambda d, e: e.matches(WebKit2.download_error_quark(),
                                                          WebKit2.DownloadError.CANCELLED_BY_USER)
                         or print(f"download failed: {e.message}", file=sys.stderr))

    context.connect("download-started", on_download)

    # ---- theme setting, shared by the menu and the system color scheme
    interface = None
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.interface", True) if source else None
    if schema and schema.has_key("color-scheme"):
        interface = Gio.Settings.new("org.gnome.desktop.interface")

    def system_prefers_dark():
        if interface is not None:
            return interface.get_string("color-scheme") == "prefer-dark"
        return bool(Gtk.Settings.get_default().props.gtk_theme_name.lower().endswith("-dark"))

    def theme_changed():
        for callback in state.theme_callbacks:
            callback()

    if interface is not None:
        interface.connect("changed::color-scheme", lambda *_: theme_changed())

    theme_action = Gio.SimpleAction.new_stateful("theme", GLib.VariantType.new("s"),
                                                 GLib.Variant("s", settings["theme"]))

    def on_theme(action, value):
        action.set_state(value)
        settings["theme"] = value.get_string()
        save_settings(settings)
        theme_changed()

    theme_action.connect("change-state", on_theme)
    app.add_action(theme_action)
    quit_action = Gio.SimpleAction.new("quit", None)
    quit_action.connect("activate", lambda *_: [w.close() for w in app.get_windows()])
    app.add_action(quit_action)

    app.connect("activate", on_activate)
    return app.run([sys.argv[0]])
