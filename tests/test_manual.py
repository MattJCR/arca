from __future__ import annotations

import json
from pathlib import Path

from arca.api import create_app
from arca.config import ArcaConfig, init_library
from arca.manual import (
    is_page_name,
    list_pages,
    manual_dir,
    page_path,
    render_markdown,
    render_page,
)
from arca.vendor import (
    default_lock_path,
    is_commit,
    llama_lock_entry,
    load_lock,
    write_lock_entry,
)
from tests.test_api import http_get, http_request

SAMPLE = """# Heading

A paragraph with `code`, **bold** and a link to [another page](search.md).

## Table

| Key | Value |
|---|---|
| one | two |

- first
- second

```
literal <script>
```
"""


def test_the_manual_travels_with_the_application() -> None:
    directory = manual_dir()
    assert directory is not None
    names = {page.name for page in list_pages()}
    assert {"README", "chat", "llama", "diagnostics"} <= names


def test_the_index_page_comes_first() -> None:
    assert list_pages()[0].name == "README"


def test_every_page_reports_its_own_title() -> None:
    titles = {page.name: page.title for page in list_pages()}
    assert titles["chat"]
    assert not titles["chat"].startswith("#")


def test_a_forged_page_name_is_refused() -> None:
    assert is_page_name("chat")
    assert not is_page_name("../../etc/passwd")
    assert not is_page_name("Chat")
    assert page_path("../../pyproject") is None
    assert render_page("nope") == ""


def test_markdown_renders_the_subset_the_manual_uses() -> None:
    html = render_markdown(SAMPLE)
    assert "<h2>Heading</h2>" in html
    assert "<code>code</code>" in html
    assert "<strong>bold</strong>" in html
    assert '<a href="/api/manual/search">another page</a>' in html
    assert "<table" in html and "<th>Key</th>" in html and "<td>one</td>" in html
    assert "<li>first</li>" in html
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_the_manual_endpoints_serve_the_pages(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, ArcaConfig())
    status, body = http_get(app, "/api/manual")
    assert status == 200
    pages = json.loads(body)["pages"]
    assert pages
    assert pages[0]["url"] == "/api/manual/README"
    status, page = http_get(app, "/api/manual/chat")
    assert status == 200
    assert b"<h2>" in page
    assert b'class="manual-page"' in page


def test_an_unknown_manual_page_says_so_without_failing(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, ArcaConfig())
    status, body = http_get(app, "/api/manual/nope")
    assert status == 200
    assert b"no existe" in body


def test_a_llama_commit_is_validated_before_it_reaches_the_lock() -> None:
    assert is_commit("a1b2c3d")
    assert is_commit("0123456789abcdef0123456789abcdef01234567")
    assert not is_commit("main")
    assert not is_commit("../../etc")


def test_pinning_a_forged_commit_is_refused_with_a_code(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, ArcaConfig())
    status, body = http_request(
        app, "POST", "/api/admin/vendor/llama", json_body={"commit": "main"}
    )
    assert status == 400
    assert json.loads(body)["code"] == "bad_commit"


def test_pinning_llama_writes_a_git_entry_with_its_licence(tmp_path: Path) -> None:
    lock = tmp_path / "sources.lock"
    lock.write_text(default_lock_path().read_text("utf-8"), "utf-8")
    before = {entry.key for entry in load_lock(lock)}
    entries = write_lock_entry(lock, llama_lock_entry("a1b2c3d4e5f6"))
    after = {entry.key: entry for entry in entries}
    assert "llama.cpp" not in before
    assert after["llama.cpp"].kind == "git"
    assert after["llama.cpp"].license == "MIT"
    assert after["llama.cpp"].commit == "a1b2c3d4e5f6"
    assert after["llama.cpp"].url.endswith("a1b2c3d4e5f6")
    assert set(before) <= set(after)
    again = {entry.key for entry in write_lock_entry(lock, llama_lock_entry("ffffffff"))}
    assert len(again) == len(after)
    assert load_lock(lock)[0].key <= load_lock(lock)[-1].key
