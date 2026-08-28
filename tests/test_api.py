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
) -> tuple[int, bytes]:
    messages: list[MutableMapping[str, Any]] = []
    body = json.dumps(json_body).encode() if json_body is not None else b""
    headers = [(b"host", b"testserver")]
    if json_body is not None:
        headers.append((b"content-type", b"application/json"))
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

    async def receive() -> MutableMapping[str, Any]:
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


def test_admin_catalog_marks_library_entries_and_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca.catalog import parse_entries
    from arca.config import library_db_path
    from arca.db import open_library_rw

    root = tmp_path / "library"
    init_library(root)
    feed = (Path(__file__).parent / "fixtures" / "catalog_entries.xml").read_bytes()
    monkeypatch.setattr("arca.api.fetch_entries", lambda url: parse_entries(feed))
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
    entries = {entry["book_name"]: entry for entry in json.loads(body)["entries"]}
    assert entries["wikipedia_es_all"]["in_library"] is True
    assert entries["wikipedia_es_all"]["is_update"] is True
    assert entries["devdocs_en_python"]["in_library"] is False
    assert entries["devdocs_en_python"]["is_update"] is False


def test_admin_catalog_filters_lists_categories_and_languages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca.catalog import parse_categories, parse_languages

    categories = (Path(__file__).parent / "fixtures" / "catalog_categories.xml").read_bytes()
    languages = (Path(__file__).parent / "fixtures" / "catalog_languages.xml").read_bytes()
    monkeypatch.setattr("arca.api.fetch_categories", lambda: parse_categories(categories))
    monkeypatch.setattr("arca.api.fetch_languages", lambda: parse_languages(languages))
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

    def failing() -> list[str]:
        raise CatalogError("offline")

    monkeypatch.setattr("arca.api.fetch_categories", failing)
    status, body = http_get(_app(tmp_path), "/api/admin/catalog/filters")
    assert status == 502
    assert json.loads(body)["error"] == "offline"


def test_admin_catalog_reports_network_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca.catalog import CatalogError

    def failing(url: str) -> list[object]:
        raise CatalogError("offline")

    monkeypatch.setattr("arca.api.fetch_entries", failing)
    status, body = http_get(_app(tmp_path), "/api/admin/catalog")
    assert status == 502
    assert json.loads(body)["error"] == "offline"


def _patch_resolve_entry(monkeypatch: pytest.MonkeyPatch, uuid: str) -> None:
    from arca.catalog import Resolution, find_entry, parse_entries

    feed = (Path(__file__).parent / "fixtures" / "catalog_entries.xml").read_bytes()
    entry = find_entry(parse_entries(feed), uuid)
    resolution = Resolution(
        size_bytes=100, sha256="a" * 64, mirrors=("https://m1/x.zim",), hash_missing=False
    )
    monkeypatch.setattr("arca.api.resolve_entry", lambda requested: (entry, resolution))


def test_admin_enqueue_records_and_queues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uuid = "0c45160e-f917-760a-9159-dfe3c53cdcdd"
    _patch_resolve_entry(monkeypatch, uuid)
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
    assert downloads[0]["mirror"] == "https://m1/x.zim"
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
    _patch_resolve_entry(monkeypatch, uuid)
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
    monkeypatch.setattr("arca.fetch.QueueWorker.kick", lambda self: None)
    app = _app(tmp_path)
    status, body = http_request(app, "POST", "/api/admin/vendor/download")
    assert status == 200
    assert json.loads(body)["queued"] == 5
    status, body = http_get(app, "/api/admin/downloads")
    downloads = json.loads(body)["downloads"]
    assert len(downloads) == 5
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
    assert "script-src" not in policy
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"].startswith("private")


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
