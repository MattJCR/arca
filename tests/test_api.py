import asyncio
import json
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library


def http_request(
    app: FastAPI,
    method: str,
    path: str,
    query: str = "",
    json_body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes]:
    messages: list[MutableMapping[str, Any]] = []
    if raw_body is not None:
        body = raw_body
    else:
        body = json.dumps(json_body).encode() if json_body is not None else b""
    headers = [(b"host", b"testserver")]
    if body:
        headers.append((b"content-type", content_type.encode()))
        headers.append((b"content-length", str(len(body)).encode()))
    scope: MutableMapping[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": unquote(path),
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }

    delivered = False

    async def receive() -> MutableMapping[str, Any]:
        nonlocal delivered
        if delivered:
            await asyncio.Event().wait()
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    response_body = b"".join(
        bytes(m.get("body", b"")) for m in messages if m["type"] == "http.response.body"
    )
    assert isinstance(status, int)
    return status, response_body


def http_get(app: FastAPI, path: str, query: str = "") -> tuple[int, bytes]:
    return http_request(app, "GET", path, query=query)


def http_full(app: FastAPI, path: str, query: str = "") -> tuple[dict[str, str], bytes]:
    messages: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(message)

    scope: MutableMapping[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(path),
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {bytes(k).decode().lower(): bytes(v).decode() for k, v in start["headers"]}
    body = b"".join(
        bytes(m.get("body", b"")) for m in messages if m["type"] == "http.response.body"
    )
    return headers, body


def http_headers(app: FastAPI, path: str, query: str = "") -> dict[str, str]:
    captured: dict[str, str] = {}

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            for name, value in message["headers"]:
                captured[bytes(name).decode().lower()] = bytes(value).decode()

    scope: MutableMapping[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(path),
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }
    asyncio.run(app(scope, receive, send))
    return captured


def _app(tmp_path: Path, config: ArcaConfig | None = None) -> FastAPI:
    root = tmp_path / "library"
    init_library(root)
    return create_app(root, config or ArcaConfig())


def test_diagnostics_endpoint_reports_checks_in_spanish_by_default(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/api/diagnostics")
    assert status == 200
    payload = json.loads(body)
    assert payload["language"] == "es"
    checks = {check["id"]: check for check in payload["checks"]}
    assert checks["library_path"]["state"] == "ok"
    assert checks["library_path"]["state_label"] == "correcto"


def test_diagnostics_endpoint_switches_to_english(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/api/diagnostics", query="lang=en")
    assert status == 200
    payload = json.loads(body)
    assert payload["language"] == "en"
    labels = {check["state_label"] for check in payload["checks"]}
    assert "correcto" not in labels


def test_diagnostics_endpoint_honours_configured_language(tmp_path: Path) -> None:
    _, body = http_get(_app(tmp_path, ArcaConfig(language="en")), "/api/diagnostics")
    assert json.loads(body)["language"] == "en"


def test_home_page_explains_the_empty_state(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/")
    text = body.decode("utf-8")
    assert status == 200
    assert 'lang="es"' in text
    assert "Biblioteca de conocimiento offline" in text
    assert 'class="pane pane-archive"' in text
    assert "archive.js" in text
    assert "/static/fonts/fonts.css" in text
    assert "home.library.empty" in text
    assert "/static/style.css" in text
    assert 'class="pane pane-assistant"' in text


def test_home_page_renders_in_english_on_demand(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/", query="lang=en")
    text = body.decode("utf-8")
    assert status == 200
    assert 'lang="en"' in text
    assert "Offline knowledge library" in text
    assert "There is no collection in the library yet" in text


def test_admin_page_is_bilingual_and_embeds_its_strings(tmp_path: Path) -> None:
    status_es, body_es = http_get(_app(tmp_path), "/admin")
    status_en, body_en = http_get(_app(tmp_path), "/admin", query="lang=en")
    assert status_es == status_en == 200
    text_es = body_es.decode("utf-8")
    text_en = body_en.decode("utf-8")
    assert "Administraci" in text_es
    assert "Administration" in text_en
    assert 'id="i18n-data"' in text_es
    assert "Cola de descargas" in text_es
    assert "Download queue" in text_en
    assert "/static/admin.js" in text_es
    assert 'role="tablist"' in text_es
    assert 'id="lang-select"' in text_es
    assert '<option value="en" selected>' in text_en


def test_static_css_is_served(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/static/style.css")
    assert status == 200
    assert b"--accent" in body


def test_api_schema_endpoints_are_disabled(tmp_path: Path) -> None:
    status, _ = http_get(_app(tmp_path), "/docs")
    assert status == 404
    status, _ = http_get(_app(tmp_path), "/openapi.json")
    assert status == 404


def test_admin_status_reports_counts_and_events(tmp_path: Path) -> None:
    app = _app(tmp_path)
    status, body = http_get(app, "/api/admin/status")
    assert status == 200
    payload = json.loads(body)
    assert payload["counts"]["zim"] == 0
    assert payload["counts"]["shards"] == 0
    assert payload["free_bytes"] > 0
    assert payload["worst_state"] in {"ok", "degraded"}
    assert payload["events"] == []


FIXTURES = Path(__file__).parent / "fixtures"


def _serve_kiwix(monkeypatch: pytest.MonkeyPatch) -> None:
    feed = (FIXTURES / "catalog_entries.xml").read_bytes()
    meta4 = (FIXTURES / "sample.meta4").read_bytes()

    def get(url: str) -> bytes:
        return meta4 if ".meta4" in url else feed

    monkeypatch.setattr("arca.sources.http_get", get)


def _serve_books(monkeypatch: pytest.MonkeyPatch) -> None:
    feed = (FIXTURES / "opds_books.xml").read_bytes()
    monkeypatch.setattr("arca.sources.http_get", lambda url: feed)


BOOKS_SOURCE = {
    "source_id": "books",
    "name": "A books catalogue",
    "kind": "opds",
    "base_url": "https://books.example.org",
    "entries_path": "/opds",
    "media_type": "application/epub+zip",
    "item_kind": "book",
}


def test_admin_catalog_marks_library_entries_and_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca.config import library_db_path
    from arca.db import open_library_rw

    root = tmp_path / "library"
    init_library(root)
    _serve_kiwix(monkeypatch)
    connection = open_library_rw(library_db_path(root))
    try:
        connection.execute(
            "INSERT INTO zim_files (uuid, book_name, filename, status) "
            "VALUES ('0c45160e-f917-760a-9159-dfe3c53cdcdd', 'wikipedia_es_all', "
            "'wikipedia_es_all_maxi_2025-01.zim', 'present')"
        )
        connection.commit()
    finally:
        connection.close()
    app = create_app(root, ArcaConfig())
    status, body = http_get(app, "/api/admin/catalog", query="q=wikipedia")
    assert status == 200
    payload = json.loads(body)
    entries = {entry["title"]: entry for entry in payload["entries"]}
    assert entries["Wikipedia (Spanish)"]["in_library"] is True
    assert entries["Wikipedia (Spanish)"]["is_update"] is True
    assert entries["Wikipedia (Spanish)"]["source_id"] == "kiwix"
    assert entries["Wikipedia (Spanish)"]["verified"] is True
    assert entries["DevDocs Python"]["in_library"] is False
    assert entries["DevDocs Python"]["is_update"] is False
    reader = entries["DevDocs Python"]["reader_url"]
    assert reader.startswith("https://browse.library.kiwix.org/viewer#")
    assert reader.endswith("devdocs_en_python_2026-05")
    states = {source["source_id"]: source["status"] for source in payload["sources"]}
    assert states["kiwix"] == "ok"
    assert set(states) >= {"kiwix", "standard-ebooks", "gutenberg", "openstax"}


def test_admin_catalog_filters_lists_categories_and_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca.catalog import parse_categories, parse_languages

    categories = (Path(__file__).parent / "fixtures" / "catalog_categories.xml").read_bytes()
    languages = (Path(__file__).parent / "fixtures" / "catalog_languages.xml").read_bytes()
    monkeypatch.setattr("arca.api.fetch_categories", lambda base: parse_categories(categories))
    monkeypatch.setattr("arca.api.fetch_languages", lambda base: parse_languages(languages))
    status, body = http_get(_app(tmp_path), "/api/admin/catalog/filters")
    assert status == 200
    payload = json.loads(body)
    assert payload["categories"] == ["devdocs", "gutenberg", "wikipedia"]
    spanish = next(item for item in payload["languages"] if item["code"] == "spa")
    assert spanish["name"] == "Spanish"
    assert spanish["count"] == 140


def test_admin_catalog_filters_report_network_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca.catalog import CatalogError

    def failing(base: str) -> list[str]:
        raise CatalogError("offline")

    monkeypatch.setattr("arca.api.fetch_categories", failing)
    status, body = http_get(_app(tmp_path), "/api/admin/catalog/filters")
    assert status == 502
    payload = json.loads(body)
    assert payload["error"] == "offline"
    assert payload["code"] == "unreachable"


def test_admin_catalog_reports_network_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error

    def failing(url: str) -> bytes:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("arca.sources.http_get", failing)
    status, body = http_get(_app(tmp_path), "/api/admin/catalog")
    assert status == 502
    payload = json.loads(body)
    assert "offline" in payload["error"]
    assert payload["code"] == "unreachable"


def test_admin_enqueue_records_and_queues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uuid = "0c45160e-f917-760a-9159-dfe3c53cdcdd"
    _serve_kiwix(monkeypatch)
    monkeypatch.setattr("arca.fetch.QueueWorker.kick", lambda self: None)
    app = _app(tmp_path)
    status, body = http_request(
        app,
        "POST",
        "/api/admin/downloads",
        json_body={"uuid": uuid},
    )
    assert status == 200
    download_id = json.loads(body)["id"]
    status, body = http_get(app, "/api/admin/downloads")
    downloads = json.loads(body)["downloads"]
    assert downloads[0]["id"] == download_id
    assert downloads[0]["status"] == "queued"
    assert downloads[0]["kind"] == "zim"
    assert downloads[0]["mirror"].startswith("https://mirror-one.example.org/")
    from arca.config import library_db_path
    from arca.db import open_library_ro

    connection = open_library_ro(library_db_path(tmp_path / "library"))
    try:
        event = connection.execute(
            "SELECT message FROM events WHERE message LIKE 'queued download%'"
        ).fetchone()
        assert event is not None
    finally:
        connection.close()


def test_admin_download_actions_validate_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uuid = "0c45160e-f917-760a-9159-dfe3c53cdcdd"
    _serve_kiwix(monkeypatch)
    monkeypatch.setattr("arca.fetch.QueueWorker.kick", lambda self: None)
    app = _app(tmp_path)
    _, body = http_request(
        app,
        "POST",
        "/api/admin/downloads",
        json_body={"uuid": uuid},
    )
    download_id = json.loads(body)["id"]
    status, _ = http_request(app, "POST", f"/api/admin/downloads/{download_id}/explode")
    assert status == 400
    status, body = http_request(app, "POST", f"/api/admin/downloads/{download_id}/pause")
    assert status == 200
    assert json.loads(body)["ok"] is True
    status, body = http_request(app, "POST", "/api/admin/downloads/9999/pause")
    assert json.loads(body)["ok"] is False
    status, body = http_request(app, "POST", f"/api/admin/downloads/{download_id}/delete")
    assert json.loads(body)["ok"] is True
    _, body = http_get(app, "/api/admin/downloads")
    assert json.loads(body)["downloads"] == []


def test_admin_verify_reports_results(tmp_path: Path) -> None:
    app = _app(tmp_path)
    status, body = http_request(app, "POST", "/api/admin/verify")
    assert status == 200
    assert json.loads(body)["results"] == []


def test_admin_vendor_download_queues_missing_pieces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca.vendor import default_lock_path, load_lock

    monkeypatch.setattr("arca.fetch.QueueWorker.kick", lambda self: None)
    expected = len(load_lock(default_lock_path()))
    app = _app(tmp_path)
    status, body = http_request(app, "POST", "/api/admin/vendor/download")
    assert status == 200
    assert json.loads(body)["queued"] == expected
    status, body = http_get(app, "/api/admin/downloads")
    downloads = json.loads(body)["downloads"]
    assert len(downloads) == expected
    assert {item["kind"] for item in downloads} == {"vendor"}
    assert all(item["status"] == "queued" for item in downloads)
    status, body = http_request(app, "POST", "/api/admin/vendor/download")
    assert json.loads(body)["queued"] == 0


def test_admin_vendor_reports_lock_state_and_drift(tmp_path: Path) -> None:
    app = _app(tmp_path)
    status, body = http_get(app, "/api/admin/vendor")
    assert status == 200
    payload = json.loads(body)
    assert {entry["result"] for entry in payload["entries"]} == {"missing"}
    assert {status["result"] for status in payload["drift"]} == {"ok"}


def test_admin_zim_lists_local_collections_with_their_index(tmp_path: Path) -> None:
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    build_zim(
        root / "zim" / "indexed.zim",
        [article("a.html", "Alpha", "<p>photosynthesis</p>")],
        title="Indexed",
        main_path="a.html",
    )
    build_zim(
        root / "zim" / "plain.zim",
        [article("b.html", "Beta", "<p>orogeny</p>")],
        title="Plain",
        main_path="b.html",
        fulltext=False,
    )
    (root / "zim" / "broken.zim").write_bytes(b"garbage")
    app = create_app(root, ArcaConfig())
    status, body = http_get(app, "/api/admin/zim")
    assert status == 200
    payload = json.loads(body)
    modes = {item["title"]: item["index_mode"] for item in payload["archives"]}
    assert modes == {"Indexed": "fulltext", "Plain": "title"}
    assert {item["state"] for item in payload["archives"]} == {""}
    assert [failure["filename"] for failure in payload["failures"]] == ["broken.zim"]
    assert payload["total_bytes"] > 0


def test_admin_zim_reports_the_state_recorded_in_the_database(tmp_path: Path) -> None:
    from arca.config import library_db_path
    from arca.db import open_library_rw
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    build_zim(
        root / "zim" / "known.zim",
        [article("a.html", "Alpha", "<p>photosynthesis</p>")],
        title="Known",
        main_path="a.html",
    )
    connection = open_library_rw(library_db_path(root))
    try:
        connection.execute(
            "INSERT INTO zim_files (uuid, book_name, filename, status) "
            "VALUES ('u1', 'known', 'known.zim', 'corrupt')"
        )
        connection.commit()
    finally:
        connection.close()
    status, body = http_get(create_app(root, ArcaConfig()), "/api/admin/zim")
    assert status == 200
    assert json.loads(body)["archives"][0]["state"] == "corrupt"


def test_admin_zim_is_empty_on_a_fresh_library(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/api/admin/zim")
    assert status == 200
    payload = json.loads(body)
    assert payload["archives"] == []
    assert payload["failures"] == []


def _viewer_library(tmp_path: Path) -> tuple[Path, str]:
    from arca.zim import ZimLibrary
    from tests.support import ZimArticle, article, build_zim

    root = tmp_path / "library"
    init_library(root)
    build_zim(
        root / "zim" / "viewer.zim",
        [
            article("index.html", "Main Page", "<p>Welcome</p>"),
            article("plants.html", "Plants", "<p>photosynthesis</p>"),
            ZimArticle("style/site.css", "Style", "body { color: red }", "text/css"),
            ZimArticle("img/dot.png", "Dot", "PNGDATA", "image/png"),
        ],
        title="Viewer",
        main_path="index.html",
        redirects=[("start.html", "Start", "plants.html")],
    )
    library = ZimLibrary(root)
    uuid = library.refresh()[0].uuid
    library.close()
    return root, uuid


def test_zim_article_is_served_with_a_base_tag(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    app = create_app(root, ArcaConfig())
    status, body = http_get(app, "/zim/" + uuid + "/plants.html")
    assert status == 200
    page = body.decode()
    assert '<base href="/zim/' + uuid + '/">' in page
    assert "photosynthesis" in page


def test_zim_root_serves_the_main_entry(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    status, body = http_get(create_app(root, ArcaConfig()), "/zim/" + uuid)
    assert status == 200
    assert "Welcome" in body.decode()


def test_zim_redirects_resolve_to_their_target(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    status, body = http_get(create_app(root, ArcaConfig()), "/zim/" + uuid + "/start.html")
    assert status == 200
    assert "photosynthesis" in body.decode()


def test_zim_resources_keep_their_own_mimetype(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    app = create_app(root, ArcaConfig())
    status, body = http_get(app, "/zim/" + uuid + "/style/site.css")
    assert status == 200
    assert body == b"body { color: red }"
    status, body = http_get(app, "/zim/" + uuid + "/img/dot.png")
    assert status == 200
    assert body == b"PNGDATA"


def test_zim_articles_in_subfolders_get_a_base_for_their_folder(tmp_path: Path) -> None:
    from arca.zim import ZimLibrary
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    build_zim(
        root / "zim" / "nested.zim",
        [article("sub/dir/page.html", "Page", "<p>nested</p>")],
        title="Nested",
        main_path="sub/dir/page.html",
    )
    library = ZimLibrary(root)
    uuid = library.refresh()[0].uuid
    library.close()
    status, body = http_get(create_app(root, ArcaConfig()), "/zim/" + uuid + "/sub/dir/page.html")
    assert status == 200
    assert '<base href="/zim/' + uuid + '/sub/dir/">' in body.decode()


def test_zim_errors_are_explained_in_the_active_language(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    app = create_app(root, ArcaConfig())
    missing = "/zim/00000000-0000-0000-0000-000000000000/x.html"
    status_es, body_es = http_get(app, missing)
    status_en, body_en = http_get(app, missing, query="lang=en")
    assert status_es == status_en == 404
    assert "No collection with identifier" in body_en.decode()
    assert body_es.decode() != body_en.decode()
    status, body = http_get(app, "/zim/" + uuid + "/missing.html", query="lang=en")
    assert status == 404
    assert "does not contain" in body.decode()


def test_search_endpoint_labels_every_result_with_its_source(tmp_path: Path) -> None:
    from arca.zim import ZimLibrary
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    for name, title in (("one.zim", "One"), ("two.zim", "Two"), ("three.zim", "Three")):
        build_zim(
            root / "zim" / name,
            [article("page.html", title + " page", "<p>photosynthesis</p>")],
            title=title,
            main_path="page.html",
        )
    library = ZimLibrary(root)
    scoped = library.refresh()[0].uuid
    library.close()
    app = create_app(root, ArcaConfig())
    status, body = http_get(app, "/api/search", query="q=photosynthesis")
    assert status == 200
    payload = json.loads(body)
    assert {item["source_title"] for item in payload["keyword"]} == {"One", "Two", "Three"}
    assert all(item["url"].startswith("/zim/") for item in payload["keyword"])
    assert payload["semantic"] == []
    assert payload["semantic_available"] is False
    assert len(payload["sources"]) == 3
    status, body = http_get(app, "/api/search", query="q=photosynthesis&scope=" + scoped)
    assert {item["source_id"] for item in json.loads(body)["keyword"]} == {scoped}


def test_search_endpoint_accepts_an_empty_query(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/api/search")
    assert status == 200
    payload = json.loads(body)
    assert payload["keyword"] == []
    assert payload["sources"] == []


def test_disabling_a_collection_removes_it_from_the_results_at_once(tmp_path: Path) -> None:
    from arca.zim import ZimLibrary
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    for name, title in (("one.zim", "One"), ("two.zim", "Two")):
        build_zim(
            root / "zim" / name,
            [article("page.html", title, "<p>photosynthesis</p>")],
            title=title,
            main_path="page.html",
        )
    library = ZimLibrary(root)
    uuids = {info.title: info.uuid for info in library.refresh()}
    library.close()
    app = create_app(root, ArcaConfig())

    def titles() -> set[str]:
        _, body = http_get(app, "/api/search", query="q=photosynthesis")
        return {item["source_title"] for item in json.loads(body)["keyword"]}

    assert titles() == {"One", "Two"}
    status, body = http_request(app, "POST", "/api/admin/zim/" + uuids["One"] + "/disable")
    assert status == 200
    assert json.loads(body)["enabled"] is False
    assert titles() == {"Two"}
    _, body = http_get(app, "/api/admin/zim")
    flags = {item["title"]: item["enabled"] for item in json.loads(body)["archives"]}
    assert flags == {"One": False, "Two": True}
    assert (root / "zim" / "one.zim").is_file()
    status, _ = http_request(app, "POST", "/api/admin/zim/" + uuids["One"] + "/enable")
    assert status == 200
    assert titles() == {"One", "Two"}


def test_a_disabled_collection_stays_out_even_when_named_in_the_scope(tmp_path: Path) -> None:
    from arca.zim import ZimLibrary
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    build_zim(
        root / "zim" / "only.zim",
        [article("page.html", "Only", "<p>photosynthesis</p>")],
        title="Only",
        main_path="page.html",
    )
    library = ZimLibrary(root)
    uuid = library.refresh()[0].uuid
    library.close()
    app = create_app(root, ArcaConfig())
    http_request(app, "POST", "/api/admin/zim/" + uuid + "/disable")
    _, body = http_get(app, "/api/search", query="q=photosynthesis&scope=" + uuid)
    payload = json.loads(body)
    assert payload["keyword"] == []
    assert payload["sources"] == []


def test_zim_actions_reject_unknown_verbs_and_collections(tmp_path: Path) -> None:
    app = _app(tmp_path)
    status, _ = http_request(app, "POST", "/api/admin/zim/whatever/nonsense")
    assert status == 400
    status, _ = http_request(app, "POST", "/api/admin/zim/whatever/disable")
    assert status == 404


def test_search_reports_an_empty_library_as_empty(tmp_path: Path) -> None:
    _, body = http_get(_app(tmp_path), "/api/search")
    library = json.loads(body)["library"]
    assert library["state"] == "empty"
    assert library["collections"] == 0
    assert library["unreadable"] == []


def test_search_reports_a_missing_collections_folder_as_unreachable(tmp_path: Path) -> None:
    import shutil

    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, ArcaConfig())
    shutil.rmtree(root / "zim")
    _, body = http_get(app, "/api/search")
    assert json.loads(body)["library"]["state"] == "unreachable"


def test_search_reports_collections_that_could_not_be_opened(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    (root / "zim" / "broken.zim").write_bytes(b"garbage")
    _, body = http_get(create_app(root, ArcaConfig()), "/api/search")
    library = json.loads(body)["library"]
    assert library["state"] == "unreadable"
    assert [item["filename"] for item in library["unreadable"]] == ["broken.zim"]


def test_search_reports_a_working_library_as_ok(tmp_path: Path) -> None:
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    build_zim(
        root / "zim" / "one.zim",
        [article("page.html", "Page", "<p>photosynthesis</p>")],
        title="One",
        main_path="page.html",
    )
    _, body = http_get(create_app(root, ArcaConfig()), "/api/search", query="q=photosynthesis")
    library = json.loads(body)["library"]
    assert library["state"] == "ok"
    assert library["collections"] == 1


def test_search_says_so_when_every_collection_is_disabled(tmp_path: Path) -> None:
    from arca.zim import ZimLibrary
    from tests.support import article, build_zim

    root = tmp_path / "library"
    init_library(root)
    build_zim(
        root / "zim" / "one.zim",
        [article("page.html", "Page", "<p>photosynthesis</p>")],
        title="One",
        main_path="page.html",
    )
    library = ZimLibrary(root)
    uuid = library.refresh()[0].uuid
    library.close()
    app = create_app(root, ArcaConfig())
    http_request(app, "POST", "/api/admin/zim/" + uuid + "/disable")
    _, body = http_get(app, "/api/search", query="q=photosynthesis")
    payload = json.loads(body)
    assert payload["library"]["state"] == "all_disabled"
    assert payload["library"]["collections"] == 0
    assert payload["library"]["disabled"] == 1
    assert payload["sources"] == []


def test_zim_content_carries_a_policy_that_neutralises_its_own_scripts(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    app = create_app(root, ArcaConfig())
    headers = http_headers(app, "/zim/" + uuid + "/plants.html")
    policy = headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "img-src 'self' data:" in policy
    assert "base-uri 'self'" in policy
    assert "frame-ancestors 'self'" in policy
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"].startswith("private")
    directive = next(part for part in policy.split("; ") if part.startswith("script-src"))
    assert directive.startswith("script-src 'nonce-")
    assert "'unsafe-inline'" not in directive
    assert "'self'" not in directive


def test_only_the_reader_we_inject_may_run_inside_a_zim_article(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    app = create_app(root, ArcaConfig())
    headers, raw = http_full(app, "/zim/" + uuid + "/plants.html")
    again = http_headers(app, "/zim/" + uuid + "/plants.html")["content-security-policy"]
    policy = headers["content-security-policy"]
    assert policy != again
    nonce = policy.split("script-src 'nonce-")[1].split("'")[0]
    body = raw.decode()
    assert f'<script src="/static/selection.js" nonce="{nonce}"' in body
    assert f'data-source-id="{uuid}"' in body
    assert 'data-path="plants.html"' in body
    assert body.count("nonce=") == 1


def test_zim_error_pages_carry_the_same_policy(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    app = create_app(root, ArcaConfig())
    headers = http_headers(app, "/zim/" + uuid + "/missing.html")
    assert "default-src 'none'" in headers["content-security-policy"]


def test_zim_resources_keep_the_policy_too(tmp_path: Path) -> None:
    root, uuid = _viewer_library(tmp_path)
    app = create_app(root, ArcaConfig())
    headers = http_headers(app, "/zim/" + uuid + "/style/site.css")
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["content-type"].startswith("text/css")


def test_admin_sources_lists_what_is_declared(tmp_path: Path) -> None:
    status, body = http_get(_app(tmp_path), "/api/admin/sources")
    assert status == 200
    sources = {source["source_id"]: source for source in json.loads(body)["sources"]}
    assert set(sources) == {"kiwix", "standard-ebooks", "gutenberg", "openstax"}
    assert sources["kiwix"]["status"] == "unknown"
    assert sources["kiwix"]["verifies_hash"] is True
    assert sources["kiwix"]["enabled"] is True
    assert sources["kiwix"]["filters"] == ["query", "language", "category", "flavour"]
    assert sources["gutenberg"]["enabled"] is False


def test_a_source_declared_from_the_panel_lands_in_the_configuration(tmp_path: Path) -> None:
    from arca.config import ArcaConfig as Config
    from arca.config import config_path, load_config

    root = tmp_path / "library"
    app = _app(tmp_path)
    kiwix = json.loads(http_get(app, "/api/admin/sources")[1])["sources"][0]
    declared = {key: kiwix[key] for key in ("source_id", "name", "kind", "base_url", "flavour")}
    declared["item_kind"] = "zim"
    status, body = http_request(
        app, "PUT", "/api/admin/sources", json_body={"sources": [declared, BOOKS_SOURCE]}
    )
    assert status == 200
    assert [source["source_id"] for source in json.loads(body)["sources"]] == ["kiwix", "books"]
    written = json.loads(config_path(root).read_text("utf-8"))
    assert [item["source_id"] for item in written["sources"]] == ["kiwix", "books"]
    assert written["port"] == Config().port
    reloaded = load_config(root)
    assert [source.source_id for source in reloaded.sources] == ["kiwix", "books"]


def test_a_declaration_that_makes_no_sense_is_refused(tmp_path: Path) -> None:
    app = _app(tmp_path)
    refused: list[dict[str, Any]] = [
        {"sources": []},
        {"sources": [{"source_id": "x", "kind": "torrent"}]},
    ]
    for payload in refused:
        status, body = http_request(app, "PUT", "/api/admin/sources", json_body=payload)
        assert status == 400
        assert json.loads(body)["code"] == "bad_source"


def test_probing_records_which_source_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error

    feed = (FIXTURES / "opds_books.xml").read_bytes()

    def get(url: str) -> bytes:
        if "books.example.org" in url:
            return feed
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("arca.sources.http_get", get)
    app = _app(tmp_path, ArcaConfig(sources=_two_sources()))
    status, body = http_request(app, "POST", "/api/admin/sources/probe")
    assert status == 200
    states = {source["source_id"]: source for source in json.loads(body)["sources"]}
    assert states["kiwix"]["status"] == "down"
    assert "no route to host" in states["kiwix"]["detail"]
    assert states["books"]["status"] == "unverified"
    assert states["books"]["checked_at"]


def test_a_source_that_is_down_does_not_take_the_others_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error

    feed = (FIXTURES / "opds_books.xml").read_bytes()

    def get(url: str) -> bytes:
        if "books.example.org" in url:
            return feed
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("arca.sources.http_get", get)
    app = _app(tmp_path, ArcaConfig(sources=_two_sources()))
    status, body = http_get(app, "/api/admin/catalog", query="q=plato")
    assert status == 200
    payload = json.loads(body)
    assert {entry["source_name"] for entry in payload["entries"]} == {"A books catalogue"}
    assert {entry["verified"] for entry in payload["entries"]} == {False}
    states = {source["source_id"]: source["status"] for source in payload["sources"]}
    assert states["kiwix"] == "down"
    assert states["books"] == "unverified"


def test_results_from_several_sources_are_interleaved_with_their_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kiwix = (FIXTURES / "catalog_entries.xml").read_bytes()
    books = (FIXTURES / "opds_books.xml").read_bytes()

    def get(url: str) -> bytes:
        return books if "books.example.org" in url else kiwix

    monkeypatch.setattr("arca.sources.http_get", get)
    app = _app(tmp_path, ArcaConfig(sources=_two_sources()))
    _, body = http_get(app, "/api/admin/catalog")
    entries = json.loads(body)["entries"]
    assert [entry["source_id"] for entry in entries] == ["kiwix", "books", "kiwix", "books"]
    assert all(entry["size_human"] for entry in entries)


def test_only_the_selected_sources_are_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_books(monkeypatch)
    app = _app(tmp_path, ArcaConfig(sources=_two_sources()))
    _, body = http_get(app, "/api/admin/catalog", query="sources=books")
    assert {entry["source_id"] for entry in json.loads(body)["entries"]} == {"books"}
    status, body = http_get(app, "/api/admin/catalog", query="sources=nothing")
    assert status == 400
    assert json.loads(body)["code"] == "no_source"


def test_a_source_without_filters_offers_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, ArcaConfig(sources=_two_sources()))
    status, body = http_get(app, "/api/admin/catalog/filters", query="source=books")
    assert status == 200
    assert json.loads(body) == {"categories": [], "languages": []}


def test_a_book_is_queued_into_its_own_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_books(monkeypatch)
    monkeypatch.setattr("arca.fetch.QueueWorker.kick", lambda self: None)
    app = _app(tmp_path, ArcaConfig(sources=_two_sources()))
    _, body = http_get(app, "/api/admin/catalog", query="sources=books")
    first = json.loads(body)["entries"][0]
    status, body = http_request(
        app,
        "POST",
        "/api/admin/downloads",
        json_body={"source_id": "books", "uuid": first["uuid"], "index": True},
    )
    assert status == 200
    assert json.loads(body)["hash_missing"] is True
    _, body = http_get(app, "/api/admin/downloads")
    download = json.loads(body)["downloads"][0]
    assert download["kind"] == "book"
    assert download["filename"] == "meditations.epub"
    assert download["after_done"] == "index"
    assert Path(_dest_path(tmp_path, download["id"])).parent.name == "books"


def test_an_unknown_source_or_item_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_books(monkeypatch)
    app = _app(tmp_path, ArcaConfig(sources=_two_sources()))
    status, body = http_request(
        app, "POST", "/api/admin/downloads", json_body={"source_id": "nope", "uuid": "x"}
    )
    assert status == 404
    assert json.loads(body)["code"] == "unknown_source"
    status, body = http_request(
        app, "POST", "/api/admin/downloads", json_body={"source_id": "books", "uuid": "x"}
    )
    assert status == 404
    assert json.loads(body)["code"] == "unknown_item"


def test_a_pasted_url_is_queued_like_any_other_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.fetch.QueueWorker.kick", lambda self: None)
    app = _app(tmp_path)
    status, body = http_request(
        app,
        "POST",
        "/api/admin/downloads/manual",
        json_body={"url": "https://elsewhere.example/files/handbook.pdf"},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["filename"] == "handbook.pdf"
    assert payload["hash_missing"] is True
    _, body = http_get(app, "/api/admin/downloads")
    download = json.loads(body)["downloads"][0]
    assert download["kind"] == "book"
    assert download["mirror"] == "https://elsewhere.example/files/handbook.pdf"


def test_a_pasted_url_with_a_hash_is_verified_like_any_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("arca.fetch.QueueWorker.kick", lambda self: None)
    app = _app(tmp_path)
    status, body = http_request(
        app,
        "POST",
        "/api/admin/downloads/manual",
        json_body={
            "url": "https://elsewhere.example/files/handbook.pdf",
            "name": "manual.pdf",
            "sha256": "B" * 64,
        },
    )
    assert status == 200
    assert json.loads(body) == {"id": 1, "hash_missing": False, "filename": "manual.pdf"}


def test_a_pasted_url_that_makes_no_sense_is_refused_before_queueing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    refusals = {
        "file:///C:/Windows/win.ini": "bad_url",
        "https://elsewhere.example/files/": "bad_url",
    }
    for url, code in refusals.items():
        status, body = http_request(
            app, "POST", "/api/admin/downloads/manual", json_body={"url": url}
        )
        assert status == 400, url
        assert json.loads(body)["code"] == code, url
    status, body = http_request(
        app,
        "POST",
        "/api/admin/downloads/manual",
        json_body={"url": "https://x.example/a.pdf", "sha256": "short"},
    )
    assert status == 400
    assert json.loads(body)["code"] == "bad_hash"
    status, body = http_request(
        app,
        "POST",
        "/api/admin/downloads/manual",
        json_body={"url": "https://x.example/a.pdf", "item_kind": "map"},
    )
    assert status == 400
    assert json.loads(body)["code"] == "bad_item_kind"
    _, body = http_get(app, "/api/admin/downloads")
    assert json.loads(body)["downloads"] == []


def test_the_diagnostics_report_the_state_of_every_source(tmp_path: Path) -> None:
    _, body = http_get(_app(tmp_path, ArcaConfig(sources=_two_sources())), "/api/diagnostics")
    checks = {check["id"]: check for check in json.loads(body)["checks"]}
    assert checks["remote_sources"]["state"] == "degraded"
    assert "A books catalogue" in checks["remote_sources"]["summary"]


def _two_sources() -> tuple[Any, ...]:
    from arca.sources import default_sources, parse_source

    books = parse_source(BOOKS_SOURCE)
    assert books is not None
    return (*default_sources(), books)


def _dest_path(tmp_path: Path, download_id: int) -> str:
    from arca.config import library_db_path
    from arca.db import open_library_ro

    connection = open_library_ro(library_db_path(tmp_path / "library"))
    try:
        row = connection.execute(
            "SELECT dest_path FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
        return str(row["dest_path"])
    finally:
        connection.close()
