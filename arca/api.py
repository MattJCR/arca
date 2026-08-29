from __future__ import annotations

import html
import json
import shutil
import sqlite3
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import quote

from fastapi import Body, FastAPI, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from arca import __version__
from arca.catalog import (
    CatalogEntry,
    CatalogError,
    entries_url,
    fetch_categories,
    fetch_entries,
    fetch_languages,
    format_size,
    resolve_entry,
)
from arca.chat import (
    ChatEngine,
    ChatSettings,
    read_screen,
    read_settings,
    read_settings_payload,
    reply_payload,
    settings_payload,
    write_settings,
)
from arca.config import (
    CONFIG_FILENAME,
    LOG_BACKUP_COUNT,
    LOG_FILENAME,
    LOG_MAX_BYTES,
    ArcaConfig,
    config_path,
    library_db_path,
    log_dir,
    scan_root,
    session_dir,
)
from arca.db import (
    LibraryDatabaseMissingError,
    open_library_ro,
    open_library_rw,
    read_setting,
    recent_events,
    record_event,
    write_setting,
)
from arca.diagnostics import IntegrityItem, render, run_checks, verify_integrity, worst_state
from arca.embed import EmbeddingClient, EmbeddingError
from arca.fetch import (
    QueueWorker,
    enqueue,
    list_downloads,
    queue_zim_download,
    request_cancel,
    request_delete,
    request_pause,
    request_resume,
    request_retry,
    verify_library,
)
from arca.hub import (
    HubError,
    HubFile,
    HubGatedError,
    HubModel,
    download_url,
    is_model_id,
    model_files,
    search_models,
)
from arca.i18n import Catalog, load_catalog, normalize_language
from arca.ingest import PDF_MIME, is_doc_id, original_path, render_readable
from arca.llama import (
    HEALTH_OK,
    HEALTH_WAIT_SECONDS,
    MODE_FORM,
    MODE_RAW,
    ApplyResult,
    BinaryInfo,
    HelpOption,
    LlamaServer,
    ModelFile,
    Profile,
    apply_profile,
    chat_model_file,
    default_profile,
    delete_profile,
    effective_command,
    format_command,
    grouped_help,
    help_source,
    inspect_binary,
    is_profile_name,
    last_good_profile,
    list_models,
    list_profiles,
    models_dir,
    parse_help,
    preflight,
    profile_argv,
    read_profile,
    save_profile,
    set_default_profile,
)
from arca.manual import list_pages, render_document
from arca.pipeline import (
    DEFAULT_DOC_SCOPE,
    DEFAULT_ZIM_SCOPE,
    SCOPE_CURATED,
    SCOPE_FULL,
    SCOPE_KIND_DOCS,
    SCOPE_KIND_ZIM,
    SCOPES,
    ScopeEstimate,
    estimate_scope,
    ingest_vendor,
    provisional_shards,
    read_scope,
    write_scope,
)
from arca.search import (
    MODE_BOTH,
    MODE_KEYWORD,
    SearchOutcome,
    SearchResult,
    SearchSettings,
    SourceReport,
    normalize_mode,
    read_search_payload,
    read_search_settings,
    search_library,
    write_search_settings,
)
from arca.shards import STATE_INCOMPATIBLE, Shard, ShardLibrary, ShardState
from arca.vendor import (
    VendorError,
    check_drift,
    default_lock_path,
    enqueue_vendor_downloads,
    is_commit,
    llama_lock_entry,
    load_lock,
    vendor_sources,
    verify_offline,
    write_lock_entry,
)
from arca.zim import (
    DEFAULT_LIMIT,
    ZIM_SUBDIR,
    ZimEntryNotFoundError,
    ZimError,
    ZimLibrary,
    ZimNotFoundError,
    entry_directory,
    index_mode,
    inject_base,
    is_html,
    media_type,
)

PACKAGE_DIR = Path(__file__).parent

DOWNLOAD_ACTIONS = ("pause", "resume", "cancel", "retry", "delete")
JSON_BODY = Body()
ZIM_CACHE_CONTROL = "private, max-age=86400"
ZIM_CONTENT_POLICY = (
    "default-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "media-src 'self' data:; "
    "base-uri 'self'; "
    "form-action 'none'; "
    "frame-ancestors 'self'"
)
ZIM_ACTIONS = ("enable", "disable")
MODEL_KINDS = ("chat", "embed")
SECRET_KEYS = ("hub_token",)
LLAMA_ACTIONS = ("start", "stop", "restart")
DOC_CONTENT_POLICY = "default-src 'none'; style-src 'self'; font-src 'self'; script-src 'self'"
DISABLED_ZIM_SETTING = "search.disabled_zim"
USAGE_CATEGORIES: dict[str, str] = {
    "zim": "zim",
    "docs": "docs",
    "models": "models",
    "vendor": "vendor",
}


def create_app(root: Path, config: ArcaConfig) -> FastAPI:
    llama = LlamaServer(default_url=config.chat_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        llama.stop()

    app = FastAPI(
        title="ARCA",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    zim_library = ZimLibrary(root)
    shard_library = ShardLibrary(root, config.embed_model, config.embed_dims)
    embeddings = EmbeddingClient(
        base_url=config.embed_url, model=config.embed_model, dims=config.embed_dims
    )
    worker = QueueWorker(
        library_db_path(root),
        concurrency=config.download_concurrency,
        max_attempts=config.download_max_attempts,
        before_replace=_releaser(zim_library),
    )
    binaries: dict[str, BinaryInfo] = {}

    def _binary(refresh: int) -> BinaryInfo:
        if refresh or "info" not in binaries:
            binaries["info"] = inspect_binary(root, config.llama_bin)
        return binaries["info"]

    def chat_search(query: str, scope: str) -> list[SearchResult]:
        shards = shard_library.searchable()
        outcome = search_library(
            zim_library,
            query,
            scope=_scope_ids(scope),
            mode=MODE_BOTH,
            limit=DEFAULT_LIMIT,
            disabled=_disabled_zim(root),
            shards=shards,
            query_vector=_query_vector(embeddings, query, MODE_BOTH, shards),
            settings=_search_settings(root),
        )
        return [*outcome.semantic, *outcome.keyword]

    engine = ChatEngine(
        searcher=chat_search,
        base_url=lambda: llama.base_url,
        available=lambda: llama.health() == HEALTH_OK,
    )

    @app.get("/api/diagnostics")
    def api_diagnostics(lang: str | None = None) -> JSONResponse:
        catalog = _catalog_for(config, lang)
        results = run_checks(root, config, server=llama)
        payload = {
            "language": catalog.language,
            "checks": [render(result, catalog) for result in results],
        }
        return JSONResponse(payload)

    @app.get("/api/admin/status")
    def api_admin_status(lang: str | None = None) -> JSONResponse:
        return JSONResponse(_admin_status(root, config, shard_library, llama))

    @app.get("/api/search")
    def api_search(
        q: str = "", scope: str = "", mode: str = "both", limit: int | None = None
    ) -> JSONResponse:
        shards = shard_library.searchable()
        outcome = search_library(
            zim_library,
            q,
            scope=_scope_ids(scope),
            mode=mode,
            limit=limit or DEFAULT_LIMIT,
            disabled=_disabled_zim(root),
            shards=shards,
            query_vector=_query_vector(embeddings, q, mode, shards),
            settings=_search_settings(root),
        )
        payload = _search_payload(outcome)
        payload["library"] = _library_state(root, zim_library, _disabled_zim(root))
        payload["documents"] = _documents_state(shard_library, embeddings)
        return JSONResponse(payload)

    @app.get("/api/doc/{doc_id}", response_class=HTMLResponse)
    def api_document(doc_id: str, lang: str | None = None) -> Response:
        catalog = _catalog_for(config, lang)
        if not is_doc_id(doc_id):
            return _zim_error(catalog, "doc.error.unknown", 404, doc_id=doc_id)
        found = shard_library.document_location(doc_id)
        if found is None:
            return _zim_error(catalog, "doc.error.unknown", 404, doc_id=doc_id[:12])
        shard, document = found
        page = render_readable(
            document.title,
            document.orig_name,
            doc_id,
            shard.blocks(doc_id),
            shard.unindexable_pages(doc_id),
        )
        return HTMLResponse(page, headers={"Content-Security-Policy": DOC_CONTENT_POLICY})

    @app.get("/api/doc/{doc_id}/original")
    def api_document_original(doc_id: str, lang: str | None = None) -> Response:
        catalog = _catalog_for(config, lang)
        if not is_doc_id(doc_id):
            return _zim_error(catalog, "doc.error.unknown", 404, doc_id=doc_id)
        path = original_path(root, doc_id)
        if not path.is_file():
            return _zim_error(catalog, "doc.error.original_missing", 404, doc_id=doc_id[:12])
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{doc_id[:12]}.pdf"',
                "Cache-Control": ZIM_CACHE_CONTROL,
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/zim/{uuid}")
    def zim_main_entry(uuid: str, lang: str | None = None) -> Response:
        return _serve_zim(zim_library, _catalog_for(config, lang), uuid, "")

    @app.get("/zim/{uuid}/{path:path}")
    def zim_entry(uuid: str, path: str, lang: str | None = None) -> Response:
        return _serve_zim(zim_library, _catalog_for(config, lang), uuid, path)

    @app.get("/api/admin/zim")
    def api_admin_zim() -> JSONResponse:
        return JSONResponse(_zim_payload(root, zim_library))

    @app.post("/api/admin/zim/{uuid}/{action}")
    def api_admin_zim_action(uuid: str, action: str) -> JSONResponse:
        if action not in ZIM_ACTIONS:
            return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)
        try:
            zim_library.info(uuid)
        except ZimNotFoundError:
            return JSONResponse({"error": f"unknown collection: {uuid}"}, status_code=404)
        enabled = action == "enable"
        _set_zim_enabled(root, uuid, enabled)
        return JSONResponse({"uuid": uuid, "enabled": enabled})

    @app.get("/api/admin/documents")
    def api_admin_documents() -> JSONResponse:
        return JSONResponse(_admin_documents(root, shard_library, embeddings))

    @app.get("/api/admin/models")
    def api_admin_models() -> JSONResponse:
        return JSONResponse(_admin_models(root, shard_library, config))

    @app.get("/api/admin/hub")
    def api_admin_hub(q: str = "", limit: int = 20) -> JSONResponse:
        if not q.strip():
            return JSONResponse({"models": [], "query": ""})
        try:
            found = search_models(config.hub_url, q.strip(), limit, config.hub_token)
        except HubGatedError as error:
            return JSONResponse(
                {"error": str(error), "gated": True, "code": "gated"}, status_code=403
            )
        except HubError as error:
            return JSONResponse({"error": str(error), "code": "unreachable"}, status_code=502)
        return JSONResponse({"query": q.strip(), "models": [_hub_model(item) for item in found]})

    @app.get("/api/admin/hub/{model_id:path}")
    def api_admin_hub_model(model_id: str) -> JSONResponse:
        if not is_model_id(model_id):
            return JSONResponse(
                {"error": f"not a model identifier: {model_id}", "code": "bad_model_id"},
                status_code=400,
            )
        try:
            model, files = model_files(config.hub_url, model_id, config.hub_token)
        except HubGatedError as error:
            return JSONResponse(
                {"error": str(error), "gated": True, "code": "gated"}, status_code=403
            )
        except HubError as error:
            return JSONResponse({"error": str(error), "code": "unreachable"}, status_code=502)
        payload = _hub_model(model)
        payload["files"] = [_hub_file(item) for item in files]
        payload["local"] = sorted(model.name for model in list_models(root))
        return JSONResponse(payload)

    @app.post("/api/admin/hub/download")
    def api_admin_hub_download(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        model_id = str(payload.get("model_id", ""))
        path = str(payload.get("path", ""))
        kind = str(payload.get("kind", "chat"))
        if not is_model_id(model_id):
            return JSONResponse(
                {"error": f"not a model identifier: {model_id}", "code": "bad_model_id"},
                status_code=400,
            )
        if kind not in MODEL_KINDS:
            return JSONResponse(
                {"error": f"unknown model kind: {kind}", "code": "bad_kind"}, status_code=400
            )
        filename = Path(path).name
        unsafe = ".." in path or path.startswith("/") or "\\" in path
        if unsafe or not filename.endswith(".gguf") or filename != path.rsplit("/", 1)[-1]:
            return JSONResponse(
                {"error": f"not a model file: {path}", "code": "bad_file"}, status_code=400
            )
        try:
            _, files = model_files(config.hub_url, model_id, config.hub_token)
        except HubGatedError as error:
            return JSONResponse(
                {"error": str(error), "gated": True, "code": "gated"}, status_code=403
            )
        except HubError as error:
            return JSONResponse({"error": str(error), "code": "unreachable"}, status_code=502)
        wanted = next((item for item in files if item.path == path), None)
        if wanted is None:
            return JSONResponse(
                {"error": f"unknown file: {path}", "code": "unknown_file"}, status_code=404
            )
        dest = models_dir(root, kind) / filename
        connection = open_library_rw(library_db_path(root))
        try:
            download_id = enqueue(
                connection,
                kind="model",
                ref=f"{model_id}/{filename}",
                filename=filename,
                dest_path=dest,
                size_bytes=wanted.size_bytes,
                sha256=wanted.sha256,
                mirrors=(download_url(config.hub_url, model_id, path),),
            )
            record_event(connection, "info", "models", f"queued {model_id}/{filename}")
        finally:
            connection.close()
        worker.kick()
        return JSONResponse(
            {
                "id": download_id,
                "filename": filename,
                "hash_missing": not wanted.sha256,
                "dest": str(dest),
            }
        )

    @app.get("/api/admin/scope")
    def api_admin_scope() -> JSONResponse:
        return JSONResponse(_admin_scope(root, zim_library))

    @app.put("/api/admin/scope")
    def api_admin_set_scope(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        kind = str(payload.get("source_kind", ""))
        ref = str(payload.get("source_ref", ""))
        scope = str(payload.get("scope", ""))
        if kind not in (SCOPE_KIND_ZIM, SCOPE_KIND_DOCS) or not ref:
            return JSONResponse({"error": "source_kind and source_ref required"}, status_code=400)
        if scope not in SCOPES:
            return JSONResponse({"error": f"unknown scope: {scope}"}, status_code=400)
        titles = str(payload.get("article_list", ""))
        connection = open_library_rw(library_db_path(root))
        try:
            row = write_scope(connection, kind, ref, scope, titles)
            record_event(connection, "info", "scope", f"deep index scope for {ref}: {scope}")
        finally:
            connection.close()
        return JSONResponse(
            {
                "source_kind": row.source_kind,
                "source_ref": row.source_ref,
                "scope": row.scope,
                "article_count": row.article_count,
                "estimate": _estimate_payload(estimate_scope(row.article_count)),
            }
        )

    @app.get("/api/admin/catalog")
    def api_admin_catalog(
        q: str = "", lang: str = "", category: str = "", name: str = ""
    ) -> JSONResponse:
        try:
            entries = fetch_entries(entries_url(query=q, lang=lang, category=category, name=name))
        except CatalogError as error:
            return JSONResponse({"error": str(error), "code": "unreachable"}, status_code=502)
        local = _local_zim_filenames(root)
        return JSONResponse({"entries": [_entry_payload(entry, local) for entry in entries]})

    @app.get("/api/admin/catalog/filters")
    def api_admin_catalog_filters() -> JSONResponse:
        try:
            categories = fetch_categories()
            languages = fetch_languages()
        except CatalogError as error:
            return JSONResponse({"error": str(error), "code": "unreachable"}, status_code=502)
        return JSONResponse(
            {
                "categories": categories,
                "languages": [
                    {
                        "code": language.code,
                        "name": language.name,
                        "count": language.book_count,
                    }
                    for language in languages
                ],
            }
        )

    @app.get("/api/admin/downloads")
    def api_admin_downloads() -> JSONResponse:
        try:
            connection = open_library_ro(library_db_path(root))
        except LibraryDatabaseMissingError:
            return JSONResponse({"downloads": [], "worker_running": worker.is_running()})
        try:
            rows = list_downloads(connection)
        finally:
            connection.close()
        return JSONResponse(
            {
                "downloads": [_download_payload(row) for row in rows],
                "worker_running": worker.is_running(),
            }
        )

    @app.post("/api/admin/downloads")
    def api_admin_enqueue(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        uuid = str(payload.get("uuid", ""))
        if not uuid:
            return JSONResponse({"error": "uuid required"}, status_code=400)
        try:
            entry, resolution = resolve_entry(uuid)
        except CatalogError as error:
            return JSONResponse({"error": str(error), "code": "unreachable"}, status_code=502)
        connection = open_library_rw(library_db_path(root))
        try:
            download_id = queue_zim_download(connection, root, entry, resolution)
        finally:
            connection.close()
        worker.kick()
        return JSONResponse({"id": download_id, "hash_missing": resolution.hash_missing})

    @app.post("/api/admin/downloads/{download_id}/{action}")
    def api_admin_download_action(download_id: int, action: str) -> JSONResponse:
        if action not in DOWNLOAD_ACTIONS:
            return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)
        connection = open_library_rw(library_db_path(root))
        try:
            handler = {
                "pause": request_pause,
                "resume": request_resume,
                "cancel": request_cancel,
                "retry": request_retry,
                "delete": request_delete,
            }[action]
            changed = handler(connection, download_id)
        finally:
            connection.close()
        if changed and action in ("resume", "retry"):
            worker.kick()
        return JSONResponse({"ok": changed})

    @app.post("/api/admin/verify")
    def api_admin_verify() -> JSONResponse:
        connection = open_library_rw(library_db_path(root))
        try:
            outcomes = verify_library(connection, root)
        finally:
            connection.close()
        return JSONResponse(
            {
                "results": [
                    {"uuid": item.uuid, "filename": item.filename, "result": item.result}
                    for item in outcomes
                ]
            }
        )

    @app.get("/api/admin/vendor")
    def api_admin_vendor() -> JSONResponse:
        return JSONResponse(_vendor_payload(root))

    @app.post("/api/admin/vendor/verify")
    def api_admin_vendor_verify() -> JSONResponse:
        return JSONResponse(_vendor_payload(root))

    @app.post("/api/admin/vendor/download")
    def api_admin_vendor_download() -> JSONResponse:
        try:
            entries = load_lock(default_lock_path())
        except VendorError as error:
            return JSONResponse({"error": str(error)}, status_code=500)
        connection = open_library_rw(library_db_path(root))
        try:
            queued = enqueue_vendor_downloads(connection, root, entries)
        finally:
            connection.close()
        if queued:
            worker.kick()
        return JSONResponse({"queued": queued})

    @app.post("/api/chat")
    def api_chat(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        catalog = _catalog_for(config, str(payload.get("lang") or "") or None)
        raw_screen = payload.get("screen")
        screen = read_screen(raw_screen if isinstance(raw_screen, dict) else {})
        message = str(payload.get("message", ""))
        reply = engine.reply(message, screen, _chat_settings(root), catalog)
        return JSONResponse(reply_payload(reply))

    @app.get("/api/admin/llama")
    def api_admin_llama(refresh: int = 0) -> JSONResponse:
        return JSONResponse(_llama_payload(root, config, llama, _binary(refresh)))

    @app.get("/api/admin/llama/help")
    def api_admin_llama_help() -> JSONResponse:
        text, source, error = help_source(root, _binary(0).path)
        return JSONResponse(
            {
                "source": source,
                "error": error,
                "groups": [
                    {"group": name, "options": [_option_payload(item) for item in options]}
                    for name, options in grouped_help(parse_help(text))
                ],
            }
        )

    @app.get("/api/admin/llama/profiles")
    def api_admin_llama_profiles() -> JSONResponse:
        return JSONResponse(_profiles_payload(root, _binary(0)))

    @app.put("/api/admin/llama/profiles/{name}")
    def api_admin_llama_save_profile(
        name: str, payload: dict[str, Any] = JSON_BODY
    ) -> JSONResponse:
        if not is_profile_name(name):
            return JSONResponse(
                {"error": f"invalid profile name: {name}", "code": "invalid_name"}, status_code=400
            )
        mode = str(payload.get("mode", MODE_FORM))
        if mode not in (MODE_FORM, MODE_RAW):
            return JSONResponse(
                {"error": f"unknown mode: {mode}", "code": "unknown_mode"}, status_code=400
            )
        raw_args = payload.get("args")
        profile = Profile(
            name=name,
            mode=mode,
            args=(
                {str(key): str(value) for key, value in raw_args.items()}
                if isinstance(raw_args, dict)
                else {}
            ),
            raw_cmd=str(payload.get("raw_cmd", "")),
        )
        connection = open_library_rw(library_db_path(root))
        try:
            stored = save_profile(connection, profile)
            if default_profile(connection) is None:
                set_default_profile(connection, stored.name)
                stored = read_profile(connection, stored.name) or stored
            record_event(connection, "info", "llama", f"profile saved: {name}")
        finally:
            connection.close()
        return JSONResponse(_profile_payload(stored, _binary(0)))

    @app.delete("/api/admin/llama/profiles/{name}")
    def api_admin_llama_delete_profile(name: str) -> JSONResponse:
        connection = open_library_rw(library_db_path(root))
        try:
            removed = delete_profile(connection, name)
            if removed:
                record_event(connection, "info", "llama", f"profile deleted: {name}")
        finally:
            connection.close()
        if not removed:
            return JSONResponse(
                {"error": f"unknown profile: {name}", "code": "unknown_profile"}, status_code=404
            )
        return JSONResponse({"ok": True})

    @app.post("/api/admin/llama/profiles/{name}/test")
    def api_admin_llama_test_profile(name: str) -> JSONResponse:
        info = _binary(0)
        if info.path is None:
            return JSONResponse(
                {"error": "llama-server is not available", "code": "no_binary"}, status_code=409
            )
        connection = open_library_rw(library_db_path(root))
        try:
            profile = read_profile(connection, name)
            if profile is None:
                return JSONResponse(
                    {"error": f"unknown profile: {name}", "code": "unknown_profile"},
                    status_code=404,
                )
            outcome = apply_profile(connection, llama, info.path, profile)
            record_event(
                connection,
                "info" if outcome.ok else "warning",
                "llama",
                f"profile trial {name}: {outcome.trial.stage}",
                outcome.trial.error,
            )
        finally:
            connection.close()
        return JSONResponse(_trial_payload(outcome))

    @app.post("/api/admin/llama/{action}")
    def api_admin_llama_action(action: str) -> JSONResponse:
        if action not in LLAMA_ACTIONS:
            return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)
        info = _binary(0)
        if action == "stop":
            llama.stop()
            return JSONResponse(_llama_payload(root, config, llama, info))
        if info.path is None:
            return JSONResponse(
                {"error": "llama-server is not available", "code": "no_binary"}, status_code=409
            )
        profile = _active_profile(root)
        if profile is None:
            return JSONResponse(
                {"error": "no llama profile is configured", "code": "no_profile"}, status_code=409
            )
        llama.start(effective_command(info.path, profile))
        llama.wait_healthy(HEALTH_WAIT_SECONDS)
        return JSONResponse(_llama_payload(root, config, llama, info))

    @app.post("/api/admin/vendor/llama")
    def api_admin_vendor_llama(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        commit = str(payload.get("commit", "")).strip().lower()
        if not is_commit(commit):
            return JSONResponse(
                {"error": f"not a commit: {commit}", "code": "bad_commit"}, status_code=400
            )
        try:
            entries = write_lock_entry(default_lock_path(), llama_lock_entry(commit))
        except (VendorError, OSError) as error:
            return JSONResponse({"error": str(error)}, status_code=500)
        connection = open_library_rw(library_db_path(root))
        try:
            queued = enqueue_vendor_downloads(connection, root, entries)
            record_event(connection, "info", "vendor", f"llama.cpp pinned at {commit}")
        finally:
            connection.close()
        if queued:
            worker.kick()
        return JSONResponse({"commit": commit, "queued": queued})

    @app.post("/api/admin/vendor/index")
    def api_admin_vendor_index() -> JSONResponse:
        try:
            entries = load_lock(default_lock_path())
        except VendorError as error:
            return JSONResponse({"error": str(error)}, status_code=500)
        sources = vendor_sources(root, entries)
        if not sources:
            return JSONResponse({"sealed": False, "reason": "nothing_to_index"})
        if not embeddings.available():
            return JSONResponse({"sealed": False, "reason": "embeddings_down"})
        connection = open_library_rw(library_db_path(root))
        try:
            report = ingest_vendor(root, connection, sources, embeddings)
        finally:
            connection.close()
        manifest = report.manifest
        return JSONResponse(
            {
                "sealed": report.sealed,
                "reason": "" if report.sealed else "seal_failed",
                "shard_id": report.shard_id,
                "packages": manifest.doc_count if manifest else 0,
                "chunks": manifest.chunk_count if manifest else 0,
            }
        )

    @app.get("/api/manual")
    def api_manual() -> JSONResponse:
        return JSONResponse(
            {
                "pages": [
                    {
                        "name": page.name,
                        "title": page.title,
                        "size_bytes": page.size_bytes,
                        "size_human": format_size(page.size_bytes),
                        "url": f"/api/manual/{page.name}",
                    }
                    for page in list_pages()
                ]
            }
        )

    @app.get("/api/manual/{name}", response_class=HTMLResponse)
    def api_manual_page(name: str, lang: str | None = None) -> Response:
        catalog = _catalog_for(config, lang)
        document = render_document(name, catalog.language, catalog.text("manual.missing"))
        return HTMLResponse(document, headers={"Content-Security-Policy": DOC_CONTENT_POLICY})

    @app.get("/api/admin/maintenance")
    def api_admin_maintenance() -> JSONResponse:
        return JSONResponse(_maintenance_payload(root, config))

    @app.post("/api/admin/maintenance/verify")
    def api_admin_maintenance_verify() -> JSONResponse:
        connection = open_library_rw(library_db_path(root))
        try:
            items = verify_integrity(root, config, connection)
            record_event(
                connection,
                "info",
                "maintenance",
                f"integrity check: {len(items)} elements",
            )
        finally:
            connection.close()
        return JSONResponse(
            {
                "items": [
                    {
                        "kind": item.kind,
                        "name": item.name,
                        "result": item.result,
                        "detail": item.detail,
                    }
                    for item in items
                ],
                "counts": _integrity_counts(items),
            }
        )

    @app.get("/api/admin/maintenance/config")
    def api_admin_export_config() -> JSONResponse:
        return JSONResponse(_export_config(root, config))

    @app.put("/api/admin/maintenance/config")
    def api_admin_import_config(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            return JSONResponse({"error": "settings required"}, status_code=400)
        connection = open_library_rw(library_db_path(root))
        try:
            chat = settings.get("chat")
            if isinstance(chat, dict):
                write_settings(connection, read_settings_payload(chat, read_settings(connection)))
            search = settings.get("search")
            if isinstance(search, dict):
                write_search_settings(
                    connection, read_search_payload(search, read_search_settings(connection))
                )
            record_event(connection, "info", "maintenance", "configuration imported")
        finally:
            connection.close()
        return JSONResponse(_settings_payload(root, config))

    @app.get("/api/admin/settings")
    def api_admin_settings() -> JSONResponse:
        return JSONResponse(_settings_payload(root, config))

    @app.put("/api/admin/settings")
    def api_admin_save_settings(payload: dict[str, Any] = JSON_BODY) -> JSONResponse:
        chat = payload.get("chat")
        search = payload.get("search")
        if not isinstance(chat, dict) and not isinstance(search, dict):
            return JSONResponse({"error": "chat or search settings required"}, status_code=400)
        connection = open_library_rw(library_db_path(root))
        try:
            if isinstance(chat, dict):
                write_settings(connection, read_settings_payload(chat, read_settings(connection)))
                record_event(connection, "info", "chat", "chat settings updated")
            if isinstance(search, dict):
                write_search_settings(
                    connection, read_search_payload(search, read_search_settings(connection))
                )
                record_event(connection, "info", "search", "search settings updated")
        finally:
            connection.close()
        return JSONResponse(_settings_payload(root, config))

    @app.get("/", response_class=HTMLResponse)
    def home(lang: str | None = None) -> HTMLResponse:
        catalog = _catalog_for(config, lang)
        template = Template((PACKAGE_DIR / "templates" / "index.html").read_text("utf-8"))
        page = template.substitute(
            language=catalog.language,
            title=html.escape(catalog.text("app.title")),
            tagline=html.escape(catalog.text("app.tagline")),
            lang_selector=_language_selector(catalog),
            noscript=html.escape(catalog.text("home.noscript")),
            i18n_json=_embedded_i18n(catalog, "home."),
        )
        return HTMLResponse(page)

    @app.get("/admin", response_class=HTMLResponse)
    def admin(lang: str | None = None) -> HTMLResponse:
        catalog = _catalog_for(config, lang)
        template = Template((PACKAGE_DIR / "templates" / "admin.html").read_text("utf-8"))
        page = template.substitute(
            language=catalog.language,
            title=html.escape(catalog.text("admin.title")),
            lang_selector=_language_selector(catalog),
            i18n_json=_embedded_i18n(catalog, "admin.", "diag."),
        )
        return HTMLResponse(page)

    return app


def _releaser(library: ZimLibrary) -> Callable[[Path], None]:
    def release(path: Path) -> None:
        library.release(path.name)

    return release


def _catalog_for(config: ArcaConfig, lang: str | None) -> Catalog:
    return load_catalog(normalize_language(lang) if lang else config.language)


def _embedded_i18n(catalog: Catalog, *prefixes: str) -> str:
    strings = {
        key: value
        for key, value in catalog.messages.items()
        if any(key.startswith(prefix) for prefix in prefixes)
    }
    return json.dumps(strings, ensure_ascii=False).replace("</", "<\\/")


def _admin_status(
    root: Path, config: ArcaConfig, library: ShardLibrary, server: LlamaServer
) -> dict[str, Any]:
    scan = scan_root(root)
    states = library.refresh()
    documents = sum(state.manifest.doc_count for state in states if state.manifest)
    chunks = sum(state.manifest.chunk_count for state in states if state.manifest)
    usage = {name: _dir_size(root / subdir) for name, subdir in USAGE_CATEGORIES.items()}
    disk = shutil.disk_usage(root) if root.is_dir() else None
    events: list[dict[str, str]] = []
    active_downloads = 0
    try:
        connection = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        connection = None
    if connection is not None:
        try:
            events = [
                {
                    "ts": str(row["ts"]),
                    "level": str(row["level"]),
                    "area": str(row["area"]),
                    "message": str(row["message"]),
                }
                for row in recent_events(connection, limit=20)
            ]
            active_downloads = int(
                connection.execute(
                    "SELECT COUNT(*) FROM downloads "
                    "WHERE status IN ('queued', 'running', 'paused', 'verifying')"
                ).fetchone()[0]
            )
        except sqlite3.Error:
            events = []
        finally:
            connection.close()
    results = run_checks(root, config, server=server)
    return {
        "free_bytes": disk.free if disk else 0,
        "total_bytes": disk.total if disk else 0,
        "usage": usage,
        "counts": {
            "zim": scan.zim_count,
            "shards": len(states),
            "documents": documents,
            "chunks": chunks,
            "active_downloads": active_downloads,
        },
        "worst_state": worst_state(results).value,
        "events": events,
    }


def _dir_size(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(entry.stat().st_size for entry in directory.rglob("*") if entry.is_file())


def _local_zim_filenames(root: Path) -> dict[str, str]:
    rows = _zim_file_rows(root, "book_name, filename")
    return {str(row["book_name"]): str(row["filename"]) for row in rows}


def _entry_payload(entry: CatalogEntry, local: dict[str, str]) -> dict[str, Any]:
    local_filename = local.get(entry.book_name, "")
    return {
        "uuid": entry.uuid,
        "book_name": entry.book_name,
        "filename": entry.filename,
        "title": entry.title,
        "description": entry.description,
        "language": entry.language,
        "flavour": entry.flavour,
        "size_bytes": entry.size_bytes,
        "size_human": format_size(entry.size_bytes),
        "article_count": entry.article_count,
        "has_fulltext": entry.has_fulltext,
        "in_library": bool(local_filename),
        "is_update": bool(local_filename) and local_filename != entry.filename,
    }


def _download_payload(row: sqlite3.Row) -> dict[str, Any]:
    mirrors: list[str] = []
    try:
        parsed = json.loads(str(row["mirrors"] or "[]"))
        if isinstance(parsed, list):
            mirrors = [item for item in parsed if isinstance(item, str)]
    except ValueError:
        mirrors = []
    mirror_index = int(row["mirror_index"] or 0)
    mirror = mirrors[mirror_index % len(mirrors)] if mirrors else ""
    return {
        "id": int(row["id"]),
        "kind": str(row["kind"]),
        "ref": str(row["ref"]),
        "filename": str(row["filename"]),
        "status": str(row["status"]),
        "bytes_done": int(row["bytes_done"] or 0),
        "size_bytes": row["size_bytes"],
        "size_human": format_size(row["size_bytes"]),
        "done_human": format_size(int(row["bytes_done"] or 0)),
        "mirror": mirror,
        "attempts": int(row["attempts"] or 0),
        "last_error": str(row["last_error"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _library_state(root: Path, library: ZimLibrary, disabled: Sequence[str] = ()) -> dict[str, Any]:
    directory = root / ZIM_SUBDIR
    failures = library.failures()
    infos = library.infos()
    excluded = set(disabled)
    enabled = [info for info in infos if info.uuid not in excluded]
    if not directory.is_dir():
        state = "unreachable"
    elif enabled:
        state = "ok"
    elif infos:
        state = "all_disabled"
    elif failures:
        state = "unreadable"
    else:
        state = "empty"
    return {
        "state": state,
        "collections": len(enabled),
        "disabled": len(infos) - len(enabled),
        "unreadable": [
            {"filename": failure.filename, "error": failure.error} for failure in failures
        ],
    }


def _disabled_zim(root: Path) -> tuple[str, ...]:
    try:
        connection = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        return ()
    try:
        raw = read_setting(connection, DISABLED_ZIM_SETTING, "[]")
    except sqlite3.Error:
        return ()
    finally:
        connection.close()
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, str))


def _set_zim_enabled(root: Path, uuid: str, enabled: bool) -> None:
    connection = open_library_rw(library_db_path(root))
    try:
        current = set(_disabled_setting(connection))
        if enabled:
            current.discard(uuid)
        else:
            current.add(uuid)
        write_setting(connection, DISABLED_ZIM_SETTING, json.dumps(sorted(current)))
        state = "enabled" if enabled else "disabled"
        record_event(connection, "info", "zim", f"collection {state} for search: {uuid}")
    finally:
        connection.close()


def _disabled_setting(connection: sqlite3.Connection) -> list[str]:
    try:
        parsed: object = json.loads(read_setting(connection, DISABLED_ZIM_SETTING, "[]"))
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str)]


def _estimate_payload(estimate: ScopeEstimate) -> dict[str, Any]:
    return {
        "chunks": estimate.chunks,
        "seconds": round(estimate.seconds, 1),
        "bytes": estimate.bytes,
        "size_human": format_size(estimate.bytes),
    }


def _doc_link(doc_id: str, suffix: str = "") -> str:
    return f"/api/doc/{quote(doc_id, safe='')}{suffix}"


def _admin_documents(root: Path, library: ShardLibrary, client: EmbeddingClient) -> dict[str, Any]:
    states = library.refresh()
    documents: list[dict[str, Any]] = []
    for state in states:
        shard = library.shard(state.shard_id)
        if shard is None:
            continue
        for row in shard.documents():
            documents.append(
                {
                    "doc_id": row.doc_id,
                    "title": row.title,
                    "orig_name": row.orig_name,
                    "mime": row.mime,
                    "pages": row.pages,
                    "imported_at": row.imported_at,
                    "shard_id": state.shard_id,
                    "unindexable_pages": shard.unindexable_pages(row.doc_id),
                    "readable_url": _doc_link(row.doc_id),
                    "original_url": (
                        _doc_link(row.doc_id, "/original") if row.mime == PDF_MIME else ""
                    ),
                }
            )
    jobs, provisional = _ingest_state(root)
    return {
        "documents": documents,
        "shards": [_shard_payload(state) for state in states],
        "jobs": jobs,
        "provisional": provisional,
        "embedding_server": client.base_url,
        "embedding_available": client.available(),
    }


def _shard_payload(state: ShardState) -> dict[str, Any]:
    manifest = state.manifest
    return {
        "shard_id": state.shard_id,
        "kind": manifest.kind if manifest else "",
        "state": state.state,
        "reason": state.reason,
        "sealed_at": manifest.sealed_at if manifest else "",
        "doc_count": manifest.doc_count if manifest else 0,
        "chunk_count": manifest.chunk_count if manifest else 0,
        "embed_model": manifest.embed_model if manifest else "",
        "embed_dims": manifest.embed_dims if manifest else 0,
    }


def _ingest_state(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        connection = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        return [], []
    try:
        rows = connection.execute(
            "SELECT shard_id, source_path, sha256, pages, status, last_error, updated_at "
            "FROM ingest_jobs ORDER BY id DESC LIMIT 50"
        ).fetchall()
        pending = provisional_shards(connection, root)
    except sqlite3.Error:
        return [], []
    finally:
        connection.close()
    jobs = [
        {
            "shard_id": str(row["shard_id"] or ""),
            "source": Path(str(row["source_path"])).name,
            "doc_id": str(row["sha256"] or ""),
            "pages": int(row["pages"] or 0),
            "status": str(row["status"] or ""),
            "error": str(row["last_error"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]
    return jobs, pending


def _admin_models(root: Path, library: ShardLibrary, config: ArcaConfig) -> dict[str, Any]:
    states = library.refresh()
    return {
        "active_embed_model": config.embed_model,
        "active_embed_dims": config.embed_dims,
        "models": [
            {
                "name": model.name,
                "kind": model.kind,
                "size_bytes": model.size_bytes,
                "size_human": format_size(model.size_bytes),
                "architecture": model.architecture,
                "quantization": model.quantization,
                "context_length": model.context_length,
                "embedding_length": model.embedding_length,
                "block_count": model.block_count,
                "error": model.error,
            }
            for model in list_models(root)
        ],
        "mismatched_shards": [
            _shard_payload(state) for state in states if state.state == STATE_INCOMPATIBLE
        ],
    }


def _admin_scope(root: Path, library: ZimLibrary) -> dict[str, Any]:
    try:
        connection: sqlite3.Connection | None = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        connection = None
    sources: list[dict[str, Any]] = [
        {
            "source_kind": SCOPE_KIND_DOCS,
            "source_ref": SCOPE_KIND_DOCS,
            "title": SCOPE_KIND_DOCS,
            "articles": 0,
            "scope": DEFAULT_DOC_SCOPE,
            "article_count": 0,
            "fixed": True,
            "estimate": _estimate_payload(estimate_scope(0)),
        }
    ]
    for info in library.refresh():
        scope = DEFAULT_ZIM_SCOPE
        curated = 0
        if connection is not None:
            row = read_scope(connection, SCOPE_KIND_ZIM, info.uuid, DEFAULT_ZIM_SCOPE)
            scope = row.scope
            curated = row.article_count
        if scope == SCOPE_CURATED:
            articles = curated
        elif scope == SCOPE_FULL:
            articles = info.article_count
        else:
            articles = 0
        sources.append(
            {
                "source_kind": SCOPE_KIND_ZIM,
                "source_ref": info.uuid,
                "title": info.title,
                "articles": info.article_count,
                "scope": scope,
                "article_count": curated,
                "fixed": False,
                "estimate": _estimate_payload(estimate_scope(articles)),
            }
        )
    if connection is not None:
        connection.close()
    return {"sources": sources, "scopes": list(SCOPES)}


def _query_vector(
    client: EmbeddingClient, query: str, mode: str, shards: Sequence[Shard]
) -> list[float] | None:
    if not query.strip() or normalize_mode(mode) == MODE_KEYWORD:
        return None
    if not any(shard.state.searchable_by_vector for shard in shards):
        return None
    try:
        return client.embed_query(query)
    except EmbeddingError:
        return None


def _documents_state(library: ShardLibrary, client: EmbeddingClient) -> dict[str, Any]:
    states = library.refresh()
    return {
        "shards": [
            {
                "shard_id": state.shard_id,
                "state": state.state,
                "reason": state.reason,
                "kind": state.manifest.kind if state.manifest else "",
                "doc_count": state.manifest.doc_count if state.manifest else 0,
                "chunk_count": state.manifest.chunk_count if state.manifest else 0,
                "embed_model": state.manifest.embed_model if state.manifest else "",
                "sealed_at": state.manifest.sealed_at if state.manifest else "",
            }
            for state in states
        ],
        "semantic_ready": any(state.searchable_by_vector for state in states),
        "embedding_server": client.base_url,
    }


def _scope_ids(scope: str) -> tuple[str, ...]:
    return tuple(item for item in (part.strip() for part in scope.split(",")) if item)


def _search_payload(outcome: SearchOutcome) -> dict[str, Any]:
    return {
        "query": outcome.query,
        "mode": outcome.mode,
        "keyword": [_result_payload(result) for result in outcome.keyword],
        "semantic": [_result_payload(result) for result in outcome.semantic],
        "semantic_available": outcome.semantic_available,
        "sources": [_source_payload(report) for report in outcome.sources],
    }


def _result_payload(result: SearchResult) -> dict[str, Any]:
    return {
        "source_kind": result.source_kind,
        "source_id": result.source_id,
        "source_title": result.source_title,
        "title": result.title,
        "path": result.path,
        "url": result.url,
        "rank": result.rank,
        "doc_id": result.doc_id,
        "anchor": result.anchor,
        "page": result.page,
        "snippet": result.snippet,
        "original_url": result.original_url,
        "score": result.score,
    }


def _source_payload(report: SourceReport) -> dict[str, Any]:
    return {
        "kind": report.kind,
        "id": report.source_id,
        "title": report.title,
        "mode": report.mode,
        "estimated_matches": report.estimated_matches,
        "returned": report.returned,
        "error": report.error,
    }


def _serve_zim(library: ZimLibrary, catalog: Catalog, uuid: str, path: str) -> Response:
    try:
        info = library.info(uuid)
    except ZimNotFoundError:
        return _zim_error(catalog, "zim.error.unknown_archive", 404, uuid=uuid)
    target = path or info.main_path
    if not target:
        return _zim_error(catalog, "zim.error.no_main_entry", 404, title=info.title)
    try:
        content = library.content(uuid, target)
    except ZimEntryNotFoundError:
        return _zim_error(catalog, "zim.error.unknown_entry", 404, path=target, title=info.title)
    except ZimError:
        return _zim_error(catalog, "zim.error.unreadable", 503, title=info.title)
    body = content.data
    if is_html(content.mimetype):
        body = inject_base(body, _zim_base_href(uuid, content.path))
    return Response(
        content=body,
        media_type=media_type(content.mimetype),
        headers={
            "Cache-Control": ZIM_CACHE_CONTROL,
            "Content-Security-Policy": ZIM_CONTENT_POLICY,
            "X-Content-Type-Options": "nosniff",
        },
    )


def _zim_base_href(uuid: str, path: str) -> str:
    directory = quote(entry_directory(path), safe="/")
    return f"/zim/{quote(uuid, safe='')}/{directory}"


def _zim_error(catalog: Catalog, key: str, status_code: int, **params: object) -> HTMLResponse:
    message = html.escape(catalog.text(key, **params))
    title = html.escape(catalog.text("zim.error.heading"))
    page = (
        f'<!doctype html><html lang="{catalog.language}"><head><meta charset="utf-8">'
        f"<title>{title}</title>"
        '<link rel="stylesheet" href="/static/style.css"></head>'
        f'<body class="zim-error"><h1>{title}</h1><p>{message}</p></body></html>'
    )
    return HTMLResponse(
        page,
        status_code=status_code,
        headers={"Content-Security-Policy": ZIM_CONTENT_POLICY},
    )


def _zim_payload(root: Path, library: ZimLibrary) -> dict[str, Any]:
    infos = library.refresh()
    statuses = _zim_statuses(root)
    flavours = _zim_flavours(root)
    disabled = set(_disabled_zim(root))
    total_bytes = sum(info.size_bytes for info in infos)
    return {
        "archives": [
            {
                "uuid": info.uuid,
                "filename": info.filename,
                "title": info.title,
                "description": info.description,
                "language": info.language,
                "article_count": info.article_count,
                "media_count": info.media_count,
                "has_fulltext": info.has_fulltext,
                "index_mode": index_mode(info),
                "size_bytes": info.size_bytes,
                "size_human": format_size(info.size_bytes),
                "state": statuses.get(info.filename, ""),
                "flavour": flavours.get(info.filename, ""),
                "enabled": info.uuid not in disabled,
            }
            for info in infos
        ],
        "failures": [
            {"filename": failure.filename, "error": failure.error} for failure in library.failures()
        ],
        "total_bytes": total_bytes,
        "total_human": format_size(total_bytes),
    }


def _zim_statuses(root: Path) -> dict[str, str]:
    known = ("present", "corrupt", "missing")
    return {
        str(row["filename"]): str(row["status"])
        for row in _zim_file_rows(root, "filename, status")
        if str(row["status"] or "") in known
    }


def _zim_flavours(root: Path) -> dict[str, str]:
    return {
        str(row["filename"]): str(row["flavour"] or "")
        for row in _zim_file_rows(root, "filename, flavour")
    }


def _zim_file_rows(root: Path, columns: str) -> list[sqlite3.Row]:
    try:
        connection = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        return []
    try:
        return list(connection.execute(f"SELECT {columns} FROM zim_files").fetchall())
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _vendor_payload(root: Path) -> dict[str, Any]:
    try:
        entries = load_lock(default_lock_path())
    except VendorError as error:
        return {"error": str(error), "entries": [], "drift": []}
    offline = {status.key: status for status in verify_offline(root, entries)}
    return {
        "entries": [
            {
                "key": entry.key,
                "version": entry.version,
                "kind": entry.kind,
                "license": entry.license,
                "captured_at": entry.captured_at,
                "filename": offline[entry.key].filename,
                "result": offline[entry.key].result,
            }
            for entry in entries
        ],
        "drift": [
            {
                "package": status.package,
                "installed": status.installed,
                "documented": status.documented,
                "result": status.result,
            }
            for status in check_drift(entries)
        ],
    }


def _language_selector(catalog: Catalog) -> str:
    options = []
    for language in ("es", "en"):
        label = html.escape(catalog.text(f"ui.language.{language}"))
        selected = " selected" if language == catalog.language else ""
        options.append(f'<option value="{language}"{selected}>{label}</option>')
    label = html.escape(catalog.text("ui.language.label"))
    return (
        '<form class="lang" method="get">'
        f'<label class="visually-hidden" for="lang-select">{label}</label>'
        '<select id="lang-select" name="lang" onchange="this.form.submit()">'
        + "".join(options)
        + '</select><noscript><button type="submit">OK</button></noscript></form>'
    )


def _chat_settings(root: Path) -> ChatSettings:
    try:
        connection = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        return ChatSettings()
    try:
        return read_settings(connection)
    except sqlite3.Error:
        return ChatSettings()
    finally:
        connection.close()


def _search_settings(root: Path) -> SearchSettings:
    try:
        connection = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        return SearchSettings()
    try:
        return read_search_settings(connection)
    except sqlite3.Error:
        return SearchSettings()
    finally:
        connection.close()


def _search_settings_payload(settings: SearchSettings) -> dict[str, Any]:
    return {
        "per_source": settings.per_source,
        "zim_weight": settings.zim_weight,
        "doc_weight": settings.doc_weight,
        "snippet_chars": settings.snippet_chars,
        "chunk_min_tokens": settings.chunk_min_tokens,
        "chunk_max_tokens": settings.chunk_max_tokens,
    }


def _settings_payload(root: Path, config: ArcaConfig) -> dict[str, Any]:
    return {
        "chat": settings_payload(_chat_settings(root)),
        "search": _search_settings_payload(_search_settings(root)),
        "paths": {
            "root": str(root),
            "database": str(library_db_path(root)),
            "chat_url": config.chat_url,
            "embed_url": config.embed_url,
        },
    }


def _maintenance_payload(root: Path, config: ArcaConfig) -> dict[str, Any]:
    directory = log_dir(root)
    logs = sorted(directory.glob(f"{LOG_FILENAME}*")) if directory.is_dir() else []
    return {
        "paths": {
            "root": str(root),
            "config": str(config_path(root)),
            "database": str(library_db_path(root)),
            "logs": str(directory),
            "session": str(session_dir(root, config)),
        },
        "logs": [
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "size_human": format_size(path.stat().st_size),
            }
            for path in logs
            if path.is_file()
        ],
        "rotation": {"max_bytes": LOG_MAX_BYTES, "backups": LOG_BACKUP_COUNT},
    }


def _export_config(root: Path, config: ArcaConfig) -> dict[str, Any]:
    public = {key: value for key, value in asdict(config).items() if key not in SECRET_KEYS}
    return {
        "config_file": CONFIG_FILENAME,
        "config": public,
        "settings": {
            "chat": settings_payload(_chat_settings(root)),
            "search": _search_settings_payload(_search_settings(root)),
        },
    }


def _integrity_counts(items: Sequence[IntegrityItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.result] = counts.get(item.result, 0) + 1
    return counts


def _option_payload(option: HelpOption) -> dict[str, Any]:
    return {
        "flag": option.flag,
        "aliases": list(option.aliases),
        "value": option.value,
        "boolean": option.boolean,
        "description": option.description,
        "section": option.section,
        "group": option.group,
        "default": option.default,
        "env": option.env,
    }


def _profile_payload(profile: Profile, info: BinaryInfo) -> dict[str, Any]:
    command = effective_command(info.path, profile) if info.path else profile_argv(profile)
    return {
        "name": profile.name,
        "mode": profile.mode,
        "args": dict(profile.args),
        "raw_cmd": profile.raw_cmd,
        "is_default": profile.is_default,
        "last_good": profile.last_good,
        "created_at": profile.created_at,
        "command_line": format_command(command),
        "preflight": [
            {"code": note.code, "params": note.params} for note in preflight(command, info)
        ],
    }


def _profiles_payload(root: Path, info: BinaryInfo) -> dict[str, Any]:
    profiles, active, good = _profiles(root)
    return {
        "profiles": [_profile_payload(profile, info) for profile in profiles],
        "active": active.name if active else "",
        "last_good": good.name if good else "",
    }


def _profiles(root: Path) -> tuple[list[Profile], Profile | None, Profile | None]:
    try:
        connection = open_library_ro(library_db_path(root))
    except LibraryDatabaseMissingError:
        return [], None, None
    try:
        return (
            list_profiles(connection),
            default_profile(connection),
            last_good_profile(connection),
        )
    except sqlite3.Error:
        return [], None, None
    finally:
        connection.close()


def _active_profile(root: Path) -> Profile | None:
    profiles, active, good = _profiles(root)
    if active is not None:
        return active
    if good is not None:
        return good
    return profiles[0] if profiles else None


def _trial_payload(outcome: ApplyResult) -> dict[str, Any]:
    return {
        "ok": outcome.ok,
        "stage": outcome.trial.stage,
        "reply": outcome.trial.reply,
        "error": outcome.trial.error,
        "log": list(outcome.trial.log),
        "command_line": format_command(outcome.trial.command),
        "active": outcome.active,
        "rolled_back_to": outcome.rolled_back_to,
        "rollback_ok": outcome.rollback_ok,
    }


def _model_payload(model: ModelFile | None) -> dict[str, Any]:
    if model is None:
        return {"present": False}
    return {
        "present": True,
        "name": model.name,
        "size_bytes": model.size_bytes,
        "size_human": format_size(model.size_bytes),
        "architecture": model.architecture,
        "quantization": model.quantization,
        "context_length": model.context_length,
        "block_count": model.block_count,
        "error": model.error,
    }


def _llama_payload(
    root: Path, config: ArcaConfig, server: LlamaServer, info: BinaryInfo
) -> dict[str, Any]:
    status = server.probe()
    pending = _active_profile(root)
    command = effective_command(info.path, pending) if info.path and pending else []
    return {
        "binary": {
            "present": info.present,
            "path": str(info.path) if info.path else "",
            "source": info.source,
            "version": info.version,
            "gpu": info.gpu,
            "devices": list(info.devices),
            "error": info.error,
        },
        "server": {
            "running": status.running,
            "pid": status.pid,
            "health": status.health,
            "base_url": status.base_url,
            "command_line": status.command_line,
            "started_at": status.started_at,
            "exit_code": status.exit_code,
            "error": status.error,
        },
        "log": server.log(),
        "chat_model": _model_payload(chat_model_file(root, config.chat_model)),
        "command_line": format_command(command),
        "preflight": (
            [{"code": note.code, "params": note.params} for note in preflight(command, info)]
            if command
            else []
        ),
        **_profiles_payload(root, info),
    }


def _hub_model(model: HubModel) -> dict[str, Any]:
    return {
        "model_id": model.model_id,
        "author": model.author,
        "name": model.name,
        "downloads": model.downloads,
        "likes": model.likes,
        "gated": model.gated,
        "updated_at": model.updated_at,
        "tags": list(model.tags),
    }


def _hub_file(item: HubFile) -> dict[str, Any]:
    return {
        "path": item.path,
        "quantization": item.quantization,
        "part": item.part,
        "size_bytes": item.size_bytes,
        "size_human": format_size(item.size_bytes),
        "sha256": item.sha256,
    }
