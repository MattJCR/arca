from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent
PACKAGE_MANUAL = PACKAGE_DIR / "manual_pages"
REPO_MANUAL = PACKAGE_DIR.parent / "docs" / "manual"
INDEX_PAGE = "README"
PAGE_URL = "/api/manual/"
PAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_PATTERN = re.compile(r"^```")
ROW_PATTERN = re.compile(r"^\|(.+)\|\s*$")
DIVIDER_PATTERN = re.compile(r"^\|[\s:|-]+\|\s*$")
BULLET_PATTERN = re.compile(r"^[-*]\s+(.*)$")
NUMBER_PATTERN = re.compile(r"^\d+\.\s+(.*)$")
RULE_PATTERN = re.compile(r"^-{3,}$")
CODE_SPAN = re.compile(r"`([^`]+)`")
BOLD_SPAN = re.compile(r"\*\*([^*]+)\*\*")
LINK_SPAN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
TITLE_PATTERN = re.compile(r"^#\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class ManualPage:
    name: str
    title: str
    size_bytes: int


def manual_dir() -> Path | None:
    for candidate in (PACKAGE_MANUAL, REPO_MANUAL):
        if candidate.is_dir():
            return candidate
    return None


def is_page_name(name: str) -> bool:
    return bool(PAGE_PATTERN.match(name)) and ".." not in name


def page_path(name: str) -> Path | None:
    directory = manual_dir()
    if directory is None or not is_page_name(name):
        return None
    candidate = directory / f"{name}.md"
    if not candidate.is_file():
        return None
    if candidate.parent.resolve() != directory.resolve():
        return None
    return candidate


def list_pages() -> list[ManualPage]:
    directory = manual_dir()
    if directory is None:
        return []
    pages: list[ManualPage] = []
    for path in sorted(directory.glob("*.md")):
        pages.append(ManualPage(path.stem, page_title(path), path.stat().st_size))
    return sorted(pages, key=lambda page: (page.name != INDEX_PAGE, page.name))


def page_title(path: Path) -> str:
    try:
        text = path.read_text("utf-8")
    except OSError:
        return path.stem
    found = TITLE_PATTERN.search(text)
    return found.group(1).strip() if found else path.stem


def render_page(name: str) -> str:
    path = page_path(name)
    if path is None:
        return ""
    try:
        return render_markdown(path.read_text("utf-8"))
    except OSError:
        return ""


def render_markdown(text: str) -> str:
    out: list[str] = []
    lines = text.splitlines()
    position = 0
    while position < len(lines):
        line = lines[position].rstrip()
        if FENCE_PATTERN.match(line):
            position = _fenced(lines, position, out)
            continue
        if not line.strip():
            position += 1
            continue
        if RULE_PATTERN.match(line.strip()):
            out.append("<hr>")
            position += 1
            continue
        heading = HEADING_PATTERN.match(line)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            position += 1
            continue
        if ROW_PATTERN.match(line):
            position = _table(lines, position, out)
            continue
        if BULLET_PATTERN.match(line) or NUMBER_PATTERN.match(line):
            position = _list(lines, position, out)
            continue
        position = _paragraph(lines, position, out)
    return "\n".join(out)


def _fenced(lines: list[str], position: int, out: list[str]) -> int:
    body: list[str] = []
    position += 1
    while position < len(lines) and not FENCE_PATTERN.match(lines[position]):
        body.append(lines[position])
        position += 1
    out.append("<pre><code>" + html.escape("\n".join(body)) + "</code></pre>")
    return position + 1


def _table(lines: list[str], position: int, out: list[str]) -> int:
    rows: list[list[str]] = []
    while position < len(lines) and ROW_PATTERN.match(lines[position].rstrip()):
        raw = lines[position].rstrip()
        position += 1
        if DIVIDER_PATTERN.match(raw):
            continue
        cells = [cell.strip() for cell in raw.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return position
    header, *body = rows
    out.append('<div class="table-scroll"><table class="data"><thead><tr>')
    out.extend(f"<th>{_inline(cell)}</th>" for cell in header)
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>")
    out.append("</tbody></table></div>")
    return position


def _list(lines: list[str], position: int, out: list[str]) -> int:
    ordered = NUMBER_PATTERN.match(lines[position].rstrip()) is not None
    items: list[str] = []
    while position < len(lines):
        line = lines[position].rstrip()
        bullet = NUMBER_PATTERN.match(line) if ordered else BULLET_PATTERN.match(line)
        if bullet:
            items.append(_inline(bullet.group(1)))
            position += 1
            continue
        if line.startswith("  ") and items and line.strip():
            items[-1] = f"{items[-1]} {_inline(line.strip())}"
            position += 1
            continue
        break
    tag = "ol" if ordered else "ul"
    out.append(f"<{tag}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{tag}>")
    return position


def _paragraph(lines: list[str], position: int, out: list[str]) -> int:
    body: list[str] = []
    while position < len(lines):
        line = lines[position].rstrip()
        if not line.strip() or ROW_PATTERN.match(line) or HEADING_PATTERN.match(line):
            break
        if FENCE_PATTERN.match(line) or BULLET_PATTERN.match(line) or NUMBER_PATTERN.match(line):
            break
        body.append(line.strip())
        position += 1
    if body:
        out.append(f"<p>{_inline(' '.join(body))}</p>")
    return position


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = CODE_SPAN.sub(lambda match: f"<code>{match.group(1)}</code>", escaped)
    escaped = BOLD_SPAN.sub(lambda match: f"<strong>{match.group(1)}</strong>", escaped)
    return LINK_SPAN.sub(_link, escaped)


def _link(match: re.Match[str]) -> str:
    label, target = match.group(1), match.group(2)
    if target.endswith(".md"):
        name = target.removesuffix(".md").rsplit("/", 1)[-1]
        if is_page_name(name):
            return f'<a href="{PAGE_URL}{name}">{label}</a>'
    return f'{label} <span class="seal-path">{target}</span>'


def render_document(name: str, language: str, missing: str) -> str:
    body = render_page(name)
    content = body if body else f"<p>{html.escape(missing)}</p>"
    return (
        f'<!doctype html><html lang="{html.escape(language)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(name)}</title>"
        '<link rel="stylesheet" href="/static/fonts/fonts.css">'
        '<link rel="stylesheet" href="/static/style.css">'
        f'</head><body class="manual-page">{content}</body></html>'
    )
