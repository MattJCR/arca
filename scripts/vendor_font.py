from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

FONTS = Path(__file__).resolve().parents[1] / "arca" / "static" / "fonts"
BROWSER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
STYLESHEET = "https://fonts.googleapis.com/css2?family={family}:{axis}&display=swap"
LICENCE = "https://raw.githubusercontent.com/google/fonts/main/ofl/{slug}/OFL.txt"
SUBSETS = ("latin", "latin-ext")
BLOCK = re.compile(r"/\*\s*([a-z-]+)\s*\*/\s*(@font-face\s*\{[^}]+\})")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload: bytes = response.read()
    return payload


def field(pattern: str, text: str) -> str:
    found = re.search(pattern, text)
    if found is None:
        raise SystemExit(f"the stylesheet has no {pattern}")
    return found.group(1).strip()


def face(family: str, stem: str, subset: str, weights: str, ranges: str) -> str:
    return (
        "@font-face {\n"
        f'  font-family: "{family}";\n'
        "  font-style: normal;\n"
        f"  font-weight: {weights};\n"
        "  font-display: swap;\n"
        f'  src: url("/static/fonts/{stem}-{subset}.woff2") format("woff2");\n'
        f"  unicode-range: {ranges};\n"
        "}\n"
    )


def vendor(family: str, axis: str, stem: str, weights: str) -> None:
    slug = family.replace(" ", "").lower()
    css = fetch(STYLESHEET.format(family=family.replace(" ", "+"), axis=axis)).decode("utf-8")
    blocks = {name: block for name, block in BLOCK.findall(css)}
    missing = [subset for subset in SUBSETS if subset not in blocks]
    if missing:
        raise SystemExit(f"{family} has no {missing} subset; it offers {sorted(blocks)}")
    faces = []
    for subset in SUBSETS:
        block = blocks[subset]
        data = fetch(field(r"url\((https://[^)]+\.woff2)\)", block))
        if data[:4] != b"wOF2":
            raise SystemExit(f"{family} {subset} did not come back as woff2")
        target = FONTS / f"{stem}-{subset}.woff2"
        target.write_bytes(data)
        faces.append(
            face(family, stem, subset, weights, field(r"unicode-range:\s*([^;]+);", block))
        )
        print(f"{target.name}: {len(data)} bytes")
    licence = fetch(LICENCE.format(slug=slug)).decode("utf-8")
    if "SIL OPEN FONT LICENSE" not in licence.upper():
        raise SystemExit(f"{family} did not come back with an OFL licence")
    (FONTS / f"OFL-{stem}.txt").write_text(licence, encoding="utf-8", newline="\n")
    print(f"OFL-{stem}.txt: {len(licence)} characters")
    stylesheet = FONTS / "fonts.css"
    current = stylesheet.read_text("utf-8")
    if f'font-family: "{family}"' in current:
        print(f"fonts.css already declares {family}; the woff2 files were replaced in place")
        return
    stylesheet.write_text(current.rstrip("\n") + "\n\n" + "\n".join(faces), encoding="utf-8")
    print(f"fonts.css: {family} added")


def main(argv: list[str]) -> None:
    if len(argv) != 5:
        raise SystemExit("usage: vendor_font.py <family> <axis> <file-stem> <weight-range>")
    vendor(argv[1], argv[2], argv[3], argv[4])


main(sys.argv)
