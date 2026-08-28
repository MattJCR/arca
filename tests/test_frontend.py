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


def test_the_stylesheet_uses_the_palette_of_the_specification() -> None:
    css = (STATIC / "style.css").read_text("utf-8")
    for colour in ("#e7e9e4", "#f1f2ef", "#17242b", "#2f6f5e", "#9a6b1f", "#8e2b22"):
        assert colour in css.lower(), colour
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css


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


def test_scripts_never_write_html_directly() -> None:
    for script in STATIC.glob("*.js"):
        text = script.read_text("utf-8")
        assert "innerHTML" not in text, script.name
        assert "outerHTML" not in text, script.name
        assert "document.write" not in text, script.name
