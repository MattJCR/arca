from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from fastapi import FastAPI

from arca.api import create_app
from arca.config import ArcaConfig, init_library, library_db_path, session_dir
from arca.db import open_library_rw, record_event
from arca.history import talk_dir
from arca.llama import MODE_FORM, Profile, save_profile, set_default_profile
from arca.zim import ZimError, ZimLibrary
from tests.support import ZimArticle, article, build_zim, snapshot_tree, tree_difference
from tests.test_api import http_get, http_request

SANCTIONED_SIDECARS = ("library.db-wal", "library.db-shm")
FORBIDDEN_SUFFIXES = (".tmp", ".part", ".journal", ".lock", ".bak")
PROJECTOR = "models/chat/mmproj.gguf"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _config(tmp_path: Path) -> ArcaConfig:
    return ArcaConfig(
        session_dir=str(tmp_path / "session"), chat_url="http://127.0.0.1:1", auto_engine=False
    )


def _library(root: Path, *, with_database: bool = True) -> list[str]:
    init_library(root)
    build_zim(
        root / "zim" / "alpha.zim",
        [
            article("index.html", "Main Page", "<p>welcome to alpha</p>"),
            article("plants.html", "Plants", "<p>photosynthesis makes sugar</p>"),
            ZimArticle("style/site.css", "Style", "body { color: red }", "text/css"),
        ],
        title="Alpha",
        main_path="index.html",
        redirects=[("start.html", "Start", "plants.html")],
    )
    build_zim(
        root / "zim" / "beta.zim",
        [article("rocks.html", "Rocks", "<p>photosynthesis needs minerals</p>")],
        title="Beta",
        main_path="rocks.html",
    )
    (root / PROJECTOR).parent.mkdir(parents=True, exist_ok=True)
    (root / PROJECTOR).write_bytes(b"GGUF")
    if with_database:
        connection = open_library_rw(library_db_path(root))
        try:
            record_event(connection, "info", "test", "library prepared")
            save_profile(
                connection,
                Profile(
                    name="vision",
                    mode=MODE_FORM,
                    args={"--model": "chat.gguf", "--mmproj": str(root / PROJECTOR)},
                ),
            )
            set_default_profile(connection, "vision")
        finally:
            connection.close()
    library = ZimLibrary(root)
    uuids = [info.uuid for info in library.refresh()]
    library.close()
    return uuids


def _query_session(app: FastAPI, uuids: list[str], vision: bool = True) -> None:
    plain = [
        ("/", ""),
        ("/", "lang=en"),
        ("/api/diagnostics", ""),
        ("/api/diagnostics", "lang=en"),
        ("/static/style.css", ""),
        ("/static/archive.js", ""),
        ("/static/fonts/fonts.css", ""),
        ("/static/fonts/ibm-plex-mono-latin.woff2", ""),
        ("/api/search", urlencode({"q": "photosynthesis"})),
        ("/api/search", urlencode({"q": "photosynthesis", "scope": uuids[0]})),
        ("/api/search", urlencode({"q": "photosynthesis", "mode": "semantic"})),
        ("/api/search", ""),
    ]
    for path, query in plain:
        status, _ = http_get(app, path, query=query)
        assert status == 200, path
    for uuid in uuids:
        assert http_get(app, "/zim/" + uuid)[0] == 200
    first = uuids[0]
    for path in ("index.html", "plants.html", "start.html", "style/site.css"):
        assert http_get(app, "/zim/" + first + "/" + quote(path, safe="/"))[0] == 200
    assert http_get(app, "/zim/" + first + "/does-not-exist.html")[0] == 404
    assert http_get(app, "/zim/00000000-0000-0000-0000-000000000000/x")[0] == 404
    _talk_session(app, vision)


def _talk_session(app: FastAPI, vision: bool) -> None:
    for path in ("/talk", "/static/talk.js"):
        assert http_get(app, path)[0] == 200
    assert http_get(app, "/api/talk")[0] == 200
    status, raw = http_request(app, "POST", "/api/talk", json_body={"title": "invariant"})
    assert status == 201
    conversation_id = int(json.loads(raw)["id"])
    assert (
        http_request(
            app,
            "PUT",
            f"/api/talk/{conversation_id}",
            json_body={"title": "invariant renamed"},
        )[0]
        == 200
    )
    assert (
        http_request(
            app,
            "POST",
            f"/api/talk/{conversation_id}/message",
            json_body={"message": "does the archive stay untouched?"},
        )[0]
        == 200
    )
    assert (
        http_request(
            app,
            "POST",
            f"/api/talk/{conversation_id}/message/stream",
            json_body={"message": "and while it answers word by word?"},
        )[0]
        == 200
    )
    assert http_request(app, "POST", f"/api/talk/{conversation_id}/stop")[0] == 200
    status, raw = http_request(
        app,
        "POST",
        f"/api/talk/{conversation_id}/image",
        raw_body=PNG,
        content_type="image/png",
    )
    assert status == (201 if vision else 409), raw
    if vision:
        attachment_id = int(json.loads(raw)["id"])
        assert http_get(app, f"/api/talk/{conversation_id}/image/{attachment_id}")[0] == 200
    assert http_request(app, "DELETE", f"/api/talk/{conversation_id}")[0] == 200
    assert http_request(app, "DELETE", "/api/talk")[0] == 200


def test_a_query_session_touches_nothing_but_the_sanctioned_database_sidecars(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    uuids = _library(root)
    app = create_app(root, _config(tmp_path))
    before = snapshot_tree(root)
    _query_session(app, uuids)
    created, removed, changed = tree_difference(before, snapshot_tree(root))
    assert set(created) <= set(SANCTIONED_SIDECARS)
    assert removed == []
    assert changed == []


def test_a_query_session_creates_nothing_at_all_without_a_database(tmp_path: Path) -> None:
    root = tmp_path / "library"
    uuids = _library(root, with_database=False)
    app = create_app(root, _config(tmp_path))
    before = snapshot_tree(root)
    _query_session(app, uuids, vision=False)
    assert tree_difference(before, snapshot_tree(root)) == ([], [], [])
    assert not library_db_path(root).exists()


def test_a_query_session_never_leaves_a_temporary_or_partial_file(tmp_path: Path) -> None:
    root = tmp_path / "library"
    uuids = _library(root)
    app = create_app(root, _config(tmp_path))
    _query_session(app, uuids)
    leftovers = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.name.endswith(FORBIDDEN_SUFFIXES)
    ]
    assert leftovers == []


def test_the_collections_themselves_are_never_written_to(tmp_path: Path) -> None:
    root = tmp_path / "library"
    uuids = _library(root)
    app = create_app(root, _config(tmp_path))
    zim_dir = root / "zim"
    before = snapshot_tree(zim_dir)
    _query_session(app, uuids)
    assert tree_difference(before, snapshot_tree(zim_dir)) == ([], [], [])


def test_no_session_state_lands_under_the_root(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    for config in (ArcaConfig(), ArcaConfig(session_dir=str(root / "talk"))):
        for directory in (session_dir(root, config), talk_dir(root, config)):
            assert root.resolve() != directory.resolve()
            assert root.resolve() not in directory.resolve().parents


def test_the_talk_session_does_write_but_only_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "library"
    uuids = _library(root)
    config = _config(tmp_path)
    app = create_app(root, config)
    before = snapshot_tree(root)
    _query_session(app, uuids)
    created, removed, changed = tree_difference(before, snapshot_tree(root))
    assert set(created) <= set(SANCTIONED_SIDECARS)
    assert removed == []
    assert changed == []
    store = talk_dir(root, config)
    assert (store / "talk.sqlite").is_file()
    assert store.stat().st_size > 0


def test_a_collection_unreadable_mid_session_gives_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    uuids = _library(root)
    app = create_app(root, _config(tmp_path))
    assert http_get(app, "/zim/" + uuids[0] + "/index.html")[0] == 200

    def unplugged(self: ZimLibrary, uuid: str, path: str) -> None:
        raise ZimError("device is not connected")

    monkeypatch.setattr(ZimLibrary, "content", unplugged)
    status, body = http_get(app, "/zim/" + uuids[0] + "/index.html", query="lang=en")
    assert status == 503
    page = body.decode()
    assert "could not be read" in page
    assert "library disk" in page
    assert "Traceback" not in page


def test_a_collection_that_fails_mid_search_does_not_cancel_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from arca import search as search_module

    root = tmp_path / "library"
    uuids = _library(root)
    library = ZimLibrary(root)
    infos = library.refresh()
    original = ZimLibrary.search
    failing_uuid = infos[0].uuid

    def flaky(self: ZimLibrary, uuid: str, query: str, limit: int = 20, offset: int = 0) -> object:
        if uuid == failing_uuid:
            raise ZimError("device is not connected")
        return original(self, uuid, query, limit, offset)

    monkeypatch.setattr(ZimLibrary, "search", flaky)
    outcome = search_module.search_library(library, "photosynthesis")
    errors = {report.title: report.error for report in outcome.sources}
    assert sum(1 for error in errors.values() if error) == 1
    assert outcome.keyword
    assert len(uuids) == 2
    library.close()


def test_a_vanished_library_degrades_instead_of_crashing(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    app = create_app(root, _config(tmp_path))
    assert http_get(app, "/api/search", query=urlencode({"q": "photosynthesis"}))[0] == 200
    shutil.rmtree(root)
    status, body = http_get(app, "/api/search", query=urlencode({"q": "photosynthesis"}))
    assert status == 200
    assert b'"keyword":[]' in body.replace(b" ", b"")
    assert http_get(app, "/")[0] == 200
    status, body = http_get(app, "/api/diagnostics", query="lang=en")
    assert status == 200
    assert b'"broken"' in body
    assert not root.exists()
