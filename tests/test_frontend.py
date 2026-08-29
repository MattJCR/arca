from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "arca"
STATIC = PACKAGE / "static"
TEMPLATES = PACKAGE / "templates"
FONT_FILES = (
    "archivo-narrow-latin.woff2",
    "archivo-narrow-latin-ext.woff2",
    "source-serif-4-latin.woff2",
    "source-serif-4-latin-ext.woff2",
    "ibm-plex-mono-latin.woff2",
    "ibm-plex-mono-latin-ext.woff2",
)
LICENCE_FILES = (
    "OFL-archivo-narrow.txt",
    "OFL-source-serif-4.md",
    "OFL-ibm-plex-mono.txt",
)


def _interface_sources() -> list[Path]:
    return [
        *TEMPLATES.glob("*.html"),
        *STATIC.glob("*.css"),
        *STATIC.glob("*.js"),
        *(STATIC / "fonts").glob("*.css"),
    ]


def test_the_interface_makes_no_external_requests() -> None:
    for source in _interface_sources():
        text = source.read_text("utf-8")
        assert not re.search(r"https?://", text), source.name
        assert "//cdn" not in text, source.name


def test_the_three_typefaces_are_vendored_with_their_licences() -> None:
    fonts = STATIC / "fonts"
    for name in FONT_FILES:
        path = fonts / name
        assert path.is_file(), name
        assert path.stat().st_size > 1000, name
        assert path.read_bytes()[:4] == b"wOF2", name
    for name in LICENCE_FILES:
        licence = (fonts / name).read_text("utf-8")
        assert "SIL OPEN FONT LICENSE" in licence.upper(), name


def test_every_font_face_points_at_a_vendored_file() -> None:
    css = (STATIC / "fonts" / "fonts.css").read_text("utf-8")
    families = set(re.findall(r'font-family:\s*"([^"]+)"', css))
    assert families == {"Archivo Narrow", "Source Serif 4", "IBM Plex Mono"}
    for reference in re.findall(r'url\("([^"]+)"\)', css):
        assert reference.startswith("/static/fonts/")
        assert (STATIC / "fonts" / Path(reference).name).is_file(), reference


LIGHT_PALETTE = ("#d7d3c8", "#e6e2d8", "#171512", "#b04408", "#a1160f", "#8a5a06", "#23663a")
DARK_PALETTE = ("#0f0e0d", "#1a1917", "#ece8df", "#e8763a", "#f0685c", "#d9a63f", "#5fb377")


def test_the_stylesheet_uses_the_palette_of_the_specification() -> None:
    css = (STATIC / "style.css").read_text("utf-8").lower()
    for colour in LIGHT_PALETTE + DARK_PALETTE:
        assert colour in css, colour
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css


def test_both_themes_are_reachable_without_javascript_and_by_choice() -> None:
    css = (STATIC / "style.css").read_text("utf-8")
    assert "@media (prefers-color-scheme: dark)" in css
    assert ':root[data-theme="dark"]' in css
    assert ':root:not([data-theme="light"])' in css
    for colour in DARK_PALETTE:
        assert css.lower().count(colour) >= 2, colour


def test_the_framed_pages_follow_the_theme_the_reader_chose() -> None:
    from arca.ingest import render_readable
    from arca.manual import render_document

    tag = '<script src="/static/theme.js"></script>'
    readable = render_readable("t", "o.pdf", "a" * 64, [(1, 0, "p:0001:00", "text")])
    assert tag in readable
    assert tag in render_document("README", "es", "missing")
    assert tag in (STATIC / "pdf.html").read_text("utf-8")


def test_every_icon_a_script_draws_is_present_in_the_page_that_loads_it() -> None:
    drawn = 0
    for template in TEMPLATES.glob("*.html"):
        page = template.read_text("utf-8")
        available = set(re.findall(r'data-icon="([^"]+)"', page))
        for name in re.findall(r'<script src="/static/([^"]+\.js)"></script>', page):
            script = STATIC / name
            if not script.is_file():
                continue
            for icon in re.findall(r'svgIcon\(\s*"([^"]+)"', script.read_text("utf-8")):
                assert icon in available, f"{template.name} lacks {icon} for {name}"
                drawn += 1
    assert drawn


def test_the_theme_choice_is_offered_on_every_page_the_reader_sees() -> None:
    script = (STATIC / "theme.js").read_text("utf-8")
    assert '"arca.theme"' in script
    assert "localStorage" in script
    for template in TEMPLATES.glob("*.html"):
        assert '<script src="/static/theme.js"></script>' in template.read_text("utf-8"), (
            template.name
        )
    for page in (TEMPLATES / "index.html", TEMPLATES / "admin.html"):
        text = page.read_text("utf-8")
        assert 'data-theme-choice="light"' in text, page.name
        assert 'data-theme-choice="dark"' in text, page.name


def test_the_home_page_loads_the_vendored_fonts_and_the_archive_script() -> None:
    page = (TEMPLATES / "index.html").read_text("utf-8")
    assert '<link rel="stylesheet" href="/static/fonts/fonts.css">' in page
    assert '<script src="/static/archive.js"></script>' in page
    assert 'class="pane pane-archive"' in page
    assert 'class="pane pane-assistant"' in page
    assert 'id="viewer-frame"' in page


def test_the_viewer_frame_is_sandboxed() -> None:
    page = (TEMPLATES / "index.html").read_text("utf-8")
    frame = re.search(r"<iframe[^>]*>", page)
    assert frame is not None
    assert 'sandbox="allow-same-origin"' in frame.group(0)
    assert "allow-scripts" not in frame.group(0)


def test_the_viewer_runs_scripts_and_leans_on_the_policy_to_separate_sources() -> None:
    script = (STATIC / "archive.js").read_text("utf-8")
    assert 'const VIEWER_SANDBOX = "allow-same-origin allow-scripts";' in script
    assert 'frame.setAttribute("sandbox", VIEWER_SANDBOX)' in script


def test_the_pages_we_frame_ourselves_do_need_scripts() -> None:
    from arca.ingest import render_readable

    assert "<script" in (STATIC / "pdf.html").read_text("utf-8")
    readable = render_readable("t", "o.pdf", "a" * 64, [(1, 0, "p:0001:00", "text")])
    assert "/static/selection.js" in readable


def test_scripts_never_write_html_directly() -> None:
    for script in STATIC.glob("*.js"):
        text = script.read_text("utf-8")
        assert "innerHTML" not in text, script.name
        assert "outerHTML" not in text, script.name
        assert "document.write" not in text, script.name


def test_the_panel_carries_the_error_code_from_the_api_to_the_message() -> None:
    script = (STATIC / "admin.js").read_text("utf-8")
    assert "failure.code = payload.code" in script
    assert "function errorText(family, error)" in script
    families = set(re.findall(r'errorText\(\s*"([^"]+)"', script))
    assert families
    for family in families:
        assert f't("{family}", {{ error: error.message }})' not in script


def test_the_shared_fetch_helper_forwards_the_request_options() -> None:
    definitions = 0
    for script in STATIC.glob("*.js"):
        text = script.read_text("utf-8")
        for match in re.finditer(r"async function fetchJson\(([^)]*)\)\s*\{", text):
            definitions += 1
            assert match.group(1).replace(" ", "") == "url,options", script.name
            body = text[match.end() : match.end() + 200]
            assert "fetch(url, options)" in body, script.name
    assert definitions == len(list(STATIC.glob("*.js"))) - _scripts_without_fetch()


def _scripts_without_fetch() -> int:
    return sum(
        1
        for script in STATIC.glob("*.js")
        if "async function fetchJson(" not in script.read_text("utf-8")
    )


def test_every_region_the_scripts_rewrite_announces_itself() -> None:
    for template in TEMPLATES.glob("*.html"):
        text = template.read_text("utf-8")
        for tag in re.findall(r"<(?:p|ul|ol|div)[^>]*>", text):
            if 'class="' in tag and "message" in tag.split('class="')[1].split('"')[0].split():
                assert "aria-live" in tag, f"{template.name}: {tag}"


def test_every_page_declares_a_vendored_icon() -> None:
    icon = STATIC / "favicon.svg"
    assert icon.is_file()
    assert icon.read_text("utf-8").lstrip().startswith("<svg")
    for template in TEMPLATES.glob("*.html"):
        page = template.read_text("utf-8")
        if "<head>" not in page:
            continue
        assert '<link rel="icon" href="/static/favicon.svg"' in page, template.name
