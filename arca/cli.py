from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from arca import __version__
from arca.api import create_app
from arca.catalog import CatalogError, format_size
from arca.config import (
    ArcaConfig,
    init_library,
    library_db_path,
    load_config,
    log_dir,
    resolve_root,
    session_dir,
    setup_logging,
)
from arca.db import open_library_rw, record_event
from arca.diagnostics import CheckState, render, run_checks, worst_state
from arca.embed import EmbeddingClient
from arca.fetch import (
    AFTER_INDEX,
    KIND_BOOK,
    ProgressCallback,
    ProgressUpdate,
    QueueResult,
    is_sha256,
    queue_book_download,
    queue_manual_download,
    queue_zim_download,
    run_queue,
    verify_library,
)
from arca.formats import IngestError, UnsupportedFormatError, known_suffixes
from arca.i18n import Catalog, load_catalog, normalize_language
from arca.ingest import receive_document
from arca.opds import ALLOWED_URL_SCHEMES
from arca.pipeline import ingest_downloaded_book, ingest_vendor
from arca.shards import STATE_PROVISIONAL, STATE_SEALED, ShardLibrary, layout_drift
from arca.sources import (
    ITEM_KINDS,
    ITEM_ZIM,
    KIWIX_SOURCE_ID,
    RemoteItem,
    Source,
    SourceError,
    catalog_entry,
    filename_from_url,
    find_item,
    probe,
    resolve,
)
from arca.sources import search as search_source
from arca.vendor import (
    LockEntry,
    VendorError,
    capture_llama_help,
    check_drift,
    default_lock_path,
    is_commit,
    llama_lock_entry,
    load_lock,
    vendor_download,
    vendor_sources,
    verify_offline,
    write_lock_entry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arca")
    parser.add_argument("--version", action="version", version=f"arca {__version__}")
    parser.add_argument("--root")
    parser.add_argument("--lang", dest="ui_lang")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    subparsers.add_parser("diagnostics")
    search = subparsers.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--lang", dest="filter_lang", default="")
    search.add_argument("--category", default="")
    search.add_argument("--source", dest="source_id", default="")
    subparsers.add_parser("sources")
    add = subparsers.add_parser("add")
    add.add_argument("uuid", nargs="?", default="")
    add.add_argument("--source", dest="source_id", default="")
    add.add_argument("--url", default="")
    add.add_argument("--name", default="")
    add.add_argument("--sha256", dest="digest", default="")
    add.add_argument("--kind", dest="item_kind", default="book")
    add.add_argument("--index", action="store_true")
    run = subparsers.add_parser("run")
    run.add_argument("--concurrency", type=int)
    subparsers.add_parser("verify")
    importer = subparsers.add_parser("import")
    importer.add_argument("paths", nargs="+")
    vendor = subparsers.add_parser("vendor")
    vendor.add_argument("--verify", dest="vendor_verify", action="store_true")
    vendor.add_argument("--check-drift", dest="vendor_drift", action="store_true")
    vendor.add_argument("--llama-commit", dest="llama_commit", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui_lang: str | None = args.ui_lang
    root = resolve_root(args.root, os.environ)
    if root is None:
        catalog = load_catalog(normalize_language(ui_lang))
        print(catalog.text("cli.root_missing"), file=sys.stderr)
        return 2
    config = load_config(root)
    language = normalize_language(ui_lang) if ui_lang else config.language
    catalog = load_catalog(language)
    if args.command == "init":
        return _run_init(root, catalog)
    if args.command == "serve":
        host: str | None = args.host
        port: int | None = args.port
        if port is not None and not 1 <= port <= 65535:
            print(catalog.text("cli.invalid_port", port=port), file=sys.stderr)
            return 2
        return _run_serve(root, config, catalog, host, port)
    if args.command == "search":
        return _run_search(
            config, catalog, args.query, args.filter_lang, args.category, args.source_id
        )
    if args.command == "sources":
        return _run_sources(config, catalog)
    if args.command == "add":
        return _run_add(root, config, catalog, args)
    if args.command == "run":
        return _run_downloads(root, config, catalog, args.concurrency)
    if args.command == "verify":
        return _run_verify(root, config, catalog)
    if args.command == "import":
        return _run_import(root, catalog, args.paths)
    if args.command == "vendor":
        return _run_vendor(
            root, config, catalog, args.vendor_verify, args.vendor_drift, args.llama_commit
        )
    return _run_diagnostics(root, config, catalog)


def _run_init(root: Path, catalog: Catalog) -> int:
    created = init_library(root)
    logger = setup_logging(log_dir(root))
    connection = open_library_rw(library_db_path(root))
    try:
        message = "library initialised" if created else "init on existing library"
        record_event(connection, "info", "init", message)
    finally:
        connection.close()
    logger.info("init root=%s created=%s", root, created)
    key = "cli.init.created" if created else "cli.init.existing"
    print(catalog.text(key, root=str(root)))
    return 0


def _run_serve(
    root: Path, config: ArcaConfig, catalog: Catalog, host: str | None, port: int | None
) -> int:
    setup_logging(session_dir(root, config) / "logs")
    effective_host = host or config.host
    effective_port = port or config.port
    app = create_app(root, config)
    print(catalog.text("cli.serve.starting", host=effective_host, port=effective_port))
    uvicorn.run(app, host=effective_host, port=effective_port)
    return 0


def _run_diagnostics(root: Path, config: ArcaConfig, catalog: Catalog) -> int:
    results = run_checks(root, config)
    print(catalog.text("cli.diagnostics.header", root=str(root)))
    for result in results:
        rendered = render(result, catalog)
        print(f"[{rendered['state_label']}] {rendered['summary']}")
        for aspect in ("impact", "action"):
            if aspect in rendered:
                print(f"    {rendered[aspect]}")
    return 1 if worst_state(results) is CheckState.BROKEN else 0


def _chosen_sources(config: ArcaConfig, source_id: str) -> list[Source]:
    return [
        source
        for source in config.sources
        if source.enabled and source.searchable and (not source_id or source.source_id == source_id)
    ]


def _run_search(
    config: ArcaConfig,
    catalog: Catalog,
    query: str,
    filter_lang: str,
    category: str,
    source_id: str,
) -> int:
    chosen = _chosen_sources(config, source_id)
    if not chosen:
        print(catalog.text("cli.search.no_source", source=source_id or "-"), file=sys.stderr)
        return 1
    found: list[tuple[Source, RemoteItem]] = []
    failures = 0
    for source in chosen:
        try:
            items = search_source(source, query=query, language=filter_lang, category=category)
        except SourceError as error:
            failures += 1
            print(
                catalog.text("cli.search.source_error", source=source.name, error=str(error)),
                file=sys.stderr,
            )
            continue
        found.extend((source, item) for item in items)
    if failures == len(chosen):
        return 1
    if not found:
        print(catalog.text("cli.search.empty"))
        return 0
    print(catalog.text("cli.search.header", count=len(found)))
    for source, item in found:
        print(_search_line(catalog, source, item))
    return 0


def _search_line(catalog: Catalog, source: Source, item: RemoteItem) -> str:
    articles = str(item.article_count) if item.article_count is not None else "?"
    return catalog.text(
        "cli.search.entry",
        uuid=item.item_id,
        source=source.source_id,
        title=item.title,
        language=item.language or "?",
        flavour=item.flavour or "-",
        size=format_size(item.size_bytes),
        articles=articles,
    )


def _run_sources(config: ArcaConfig, catalog: Catalog) -> int:
    if not config.sources:
        print(catalog.text("cli.sources.empty"))
        return 0
    print(catalog.text("cli.sources.header", count=len(config.sources)))
    for source in config.sources:
        state = "unknown"
        if source.searchable and source.enabled:
            try:
                probe(source)
            except SourceError as error:
                state = "down"
                print(
                    catalog.text("cli.sources.error", source=source.name, error=str(error)),
                    file=sys.stderr,
                )
            else:
                state = "ok" if source.verifies_hash else "unverified"
        print(
            catalog.text(
                "cli.sources.entry",
                source=source.source_id,
                name=source.name,
                kind=source.kind,
                item_kind=source.item_kind,
                url=source.base_url or "-",
                state=catalog.text(f"cli.sources.state.{state}"),
            )
        )
    return 0


def _run_add(root: Path, config: ArcaConfig, catalog: Catalog, args: argparse.Namespace) -> int:
    after = AFTER_INDEX if args.index else ""
    if args.url:
        return _add_url(root, catalog, args, after)
    if not args.uuid:
        print(catalog.text("cli.add.nothing_to_add"), file=sys.stderr)
        return 2
    chosen = _chosen_sources(config, args.source_id or KIWIX_SOURCE_ID)
    if not chosen:
        print(
            catalog.text("cli.add.unknown_source", source=args.source_id or KIWIX_SOURCE_ID),
            file=sys.stderr,
        )
        return 1
    source = chosen[0]
    try:
        item = find_item(search_source(source), args.uuid)
        if item is None:
            print(catalog.text("cli.add.not_found", uuid=args.uuid), file=sys.stderr)
            return 1
        resolution = resolve(source, item)
    except CatalogError as error:
        print(catalog.text("cli.add.error", error=str(error)), file=sys.stderr)
        return 1
    connection = open_library_rw(library_db_path(root))
    try:
        if source.item_kind == ITEM_ZIM:
            download_id = queue_zim_download(connection, root, catalog_entry(item), resolution)
        else:
            download_id = queue_book_download(
                connection,
                root,
                ref=item.ref,
                filename=item.filename,
                resolution=resolution,
                after_done=after,
            )
    except ValueError as error:
        print(catalog.text("cli.add.bad_url", error=str(error)), file=sys.stderr)
        return 1
    finally:
        connection.close()
    if resolution.hash_missing:
        print(catalog.text("cli.add.no_hash_warning", filename=item.filename))
    print(catalog.text("cli.add.queued", id=download_id, filename=item.filename))
    return 0


def _add_url(root: Path, catalog: Catalog, args: argparse.Namespace, after: str) -> int:
    if urlsplit(args.url).scheme not in ALLOWED_URL_SCHEMES:
        print(catalog.text("cli.add.bad_url", error=args.url), file=sys.stderr)
        return 2
    filename = Path(args.name).name or filename_from_url(args.url)
    if not filename:
        print(catalog.text("cli.add.bad_url", error=args.url), file=sys.stderr)
        return 2
    digest = args.digest.strip().lower()
    if digest and not is_sha256(digest):
        print(catalog.text("cli.add.bad_hash", digest=args.digest), file=sys.stderr)
        return 2
    if args.item_kind not in ITEM_KINDS:
        print(catalog.text("cli.add.bad_kind", kind=args.item_kind), file=sys.stderr)
        return 2
    connection = open_library_rw(library_db_path(root))
    try:
        download_id = queue_manual_download(
            connection,
            root,
            url=args.url,
            filename=filename,
            item_kind=args.item_kind,
            sha256=digest,
            after_done=after,
        )
    except ValueError as error:
        print(catalog.text("cli.add.bad_url", error=str(error)), file=sys.stderr)
        return 2
    finally:
        connection.close()
    if not digest:
        print(catalog.text("cli.add.no_hash_warning", filename=filename))
    print(catalog.text("cli.add.queued", id=download_id, filename=filename))
    return 0


def _run_downloads(
    root: Path, config: ArcaConfig, catalog: Catalog, concurrency: int | None
) -> int:
    client = EmbeddingClient(
        base_url=config.embed_url, model=config.embed_model, dims=config.embed_dims
    )

    def on_done(result: QueueResult) -> None:
        _index_downloaded_book(root, client, catalog, result)

    results = run_queue(
        library_db_path(root),
        concurrency=concurrency or config.download_concurrency,
        max_attempts=config.download_max_attempts,
        progress=_progress_printer(catalog),
        on_done=on_done,
    )
    if not results:
        print(catalog.text("cli.run.empty"))
        return 0
    failures = 0
    for result in results:
        if result.status == "done":
            print(catalog.text("cli.run.done", filename=result.filename))
        elif result.status == "paused":
            print(catalog.text("cli.run.paused", filename=result.filename))
        else:
            failures += 1
            print(catalog.text("cli.run.failed", filename=result.filename))
    return 1 if failures else 0


def _index_downloaded_book(
    root: Path, client: EmbeddingClient, catalog: Catalog, result: QueueResult
) -> None:
    if result.kind != KIND_BOOK or result.status != "done" or result.after_done != AFTER_INDEX:
        return
    connection = open_library_rw(library_db_path(root))
    try:
        report = ingest_downloaded_book(root, connection, Path(result.dest_path), client)
    finally:
        connection.close()
    if report is None or not report.sealed:
        print(catalog.text("cli.run.not_indexed", filename=result.filename), file=sys.stderr)
        return
    print(catalog.text("cli.run.indexed", filename=result.filename, shard=report.shard_id))


def _progress_printer(catalog: Catalog) -> ProgressCallback:
    state: dict[int, tuple[float, int]] = {}
    lock = threading.Lock()

    def on_progress(update: ProgressUpdate) -> None:
        now = time.monotonic()
        with lock:
            previous = state.get(update.download_id)
            state[update.download_id] = (now, update.bytes_done)
        speed = "-"
        if previous is not None and now > previous[0] and update.bytes_done >= previous[1]:
            rate = (update.bytes_done - previous[1]) / (now - previous[0])
            speed = f"{format_size(int(rate))}/s"
        if update.size_bytes:
            percent = f"{100 * update.bytes_done / update.size_bytes:.1f}%"
        else:
            percent = "?"
        print(
            catalog.text(
                "cli.run.progress",
                filename=update.filename,
                percent=percent,
                done=format_size(update.bytes_done),
                total=format_size(update.size_bytes),
                speed=speed,
            )
        )

    return on_progress


def _run_verify(root: Path, config: ArcaConfig, catalog: Catalog) -> int:
    connection = open_library_rw(library_db_path(root))
    try:
        outcomes = verify_library(connection, root)
    finally:
        connection.close()
    problems = 0
    if not outcomes:
        print(catalog.text("cli.verify.empty"))
    else:
        print(catalog.text("cli.verify.header", count=len(outcomes)))
        for outcome in outcomes:
            print(catalog.text(f"cli.verify.{outcome.result}", filename=outcome.filename))
            if outcome.result in ("corrupt", "missing"):
                problems += 1
    problems += _verify_batches(root, config, catalog)
    return 1 if problems else 0


def _verify_batches(root: Path, config: ArcaConfig, catalog: Catalog) -> int:
    library = ShardLibrary(root, config.embed_model, config.embed_dims)
    try:
        states = library.refresh()
    finally:
        library.close()
    if not states:
        print(catalog.text("cli.verify.batches_empty"))
        return 0
    print(catalog.text("cli.verify.batches_header", count=len(states)))
    problems = 0
    for state in states:
        if state.state == STATE_PROVISIONAL:
            print(catalog.text("cli.verify.batch_provisional", shard=state.shard_id))
            continue
        if state.state != STATE_SEALED or state.manifest is None:
            print(
                catalog.text(
                    "cli.verify.batch_bad",
                    shard=state.shard_id,
                    state=state.state,
                    detail=state.reason,
                )
            )
            problems += 1
            continue
        drift = layout_drift(state.manifest)
        if drift:
            print(catalog.text("cli.verify.batch_layout", shard=state.shard_id, detail=drift))
            problems += 1
            continue
        print(
            catalog.text(
                "cli.verify.batch_ok",
                shard=state.shard_id,
                layout=state.manifest.layout or "-",
            )
        )
    return problems


def _collect_documents(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            found.extend(
                sorted(
                    path
                    for path in candidate.rglob("*")
                    if path.is_file() and path.suffix.lower() in known_suffixes()
                )
            )
        elif candidate.exists() or not found:
            found.append(candidate)
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def _run_import(root: Path, catalog: Catalog, paths: list[str]) -> int:
    documents = _collect_documents(paths)
    if not documents:
        print(catalog.text("cli.import.empty"))
        return 0
    connection = open_library_rw(library_db_path(root))
    added = 0
    duplicates = 0
    failures = 0
    try:
        for document in documents:
            try:
                outcome = receive_document(root, document, connection)
            except UnsupportedFormatError as error:
                failures += 1
                print(
                    catalog.text(
                        "cli.import.rejected",
                        filename=document.name,
                        reason=catalog.text(f"documents.refusal.{error.code}", suffix=error.suffix),
                    ),
                    file=sys.stderr,
                )
                continue
            except IngestError as error:
                failures += 1
                print(
                    catalog.text("cli.import.failed", filename=document.name, error=str(error)),
                    file=sys.stderr,
                )
                continue
            if outcome.created:
                added += 1
                key = "cli.import.added"
            else:
                duplicates += 1
                key = "cli.import.duplicate"
            print(
                catalog.text(
                    key,
                    filename=outcome.record.orig_name,
                    title=outcome.record.title,
                    pages=outcome.record.pages,
                    doc_id=outcome.record.doc_id[:12],
                )
            )
    finally:
        connection.close()
    print(catalog.text("cli.import.summary", added=added, duplicates=duplicates, failures=failures))
    return 1 if failures else 0


def _run_vendor(
    root: Path,
    config: ArcaConfig,
    catalog: Catalog,
    verify_only: bool,
    drift_only: bool,
    llama_commit: str = "",
) -> int:
    if llama_commit and not _pin_llama(catalog, llama_commit):
        return 1
    try:
        entries = load_lock(default_lock_path())
    except VendorError as error:
        print(catalog.text("cli.vendor.lock_error", error=str(error)), file=sys.stderr)
        return 1
    if verify_only:
        return _vendor_verify(root, catalog, entries)
    if drift_only:
        return _vendor_drift(catalog, entries)
    return _vendor_fetch(root, config, catalog, entries)


def _pin_llama(catalog: Catalog, commit: str) -> bool:
    if not is_commit(commit):
        print(catalog.text("cli.vendor.llama_bad_commit", commit=commit), file=sys.stderr)
        return False
    try:
        write_lock_entry(default_lock_path(), llama_lock_entry(commit))
    except (VendorError, OSError) as error:
        print(catalog.text("cli.vendor.lock_error", error=str(error)), file=sys.stderr)
        return False
    print(catalog.text("cli.vendor.llama_pinned", commit=commit))
    return True


def _vendor_verify(root: Path, catalog: Catalog, entries: list[LockEntry]) -> int:
    problems = 0
    for status in verify_offline(root, entries):
        print(
            catalog.text(
                f"cli.vendor.verify.{status.result}", entry=status.key, filename=status.filename
            )
        )
        if status.result in ("missing", "mismatch"):
            problems += 1
    return 1 if problems else 0


def _vendor_drift(catalog: Catalog, entries: list[LockEntry]) -> int:
    problems = 0
    for status in check_drift(entries):
        print(
            catalog.text(
                f"cli.vendor.drift.{status.result}",
                package=status.package,
                installed=status.installed or "-",
                documented=status.documented or "-",
            )
        )
        if status.result in ("drift", "undocumented"):
            problems += 1
    return 1 if problems else 0


def _vendor_fetch(
    root: Path, config: ArcaConfig, catalog: Catalog, entries: list[LockEntry]
) -> int:
    connection = open_library_rw(library_db_path(root))
    try:
        results = vendor_download(
            library_db_path(root),
            connection,
            root,
            entries,
            concurrency=config.download_concurrency,
            max_attempts=config.download_max_attempts,
            progress=_progress_printer(catalog),
        )
    finally:
        connection.close()
    failures = 0
    if not results:
        print(catalog.text("cli.vendor.nothing_to_download"))
    for result in results:
        if result.status == "done":
            print(catalog.text("cli.run.done", filename=result.filename))
        else:
            failures += 1
            print(catalog.text("cli.run.failed", filename=result.filename))
    help_path = capture_llama_help(root)
    if help_path is None:
        print(catalog.text("cli.vendor.llama_help_missing"))
    else:
        print(catalog.text("cli.vendor.llama_help_captured", path=str(help_path)))
    failures += _vendor_index(root, config, catalog, entries)
    return 1 if failures else 0


def _vendor_index(
    root: Path, config: ArcaConfig, catalog: Catalog, entries: list[LockEntry]
) -> int:
    sources = vendor_sources(root, entries)
    if not sources:
        print(catalog.text("cli.vendor.index_nothing"))
        return 0
    client = EmbeddingClient(
        base_url=config.embed_url, model=config.embed_model, dims=config.embed_dims
    )
    if not client.available():
        print(catalog.text("cli.vendor.index_no_server", url=config.embed_url))
        return 0
    connection = open_library_rw(library_db_path(root))
    try:
        report = ingest_vendor(root, connection, sources, client)
    finally:
        connection.close()
    if not report.sealed or report.manifest is None:
        print(catalog.text("cli.vendor.index_failed"))
        return 1
    print(
        catalog.text(
            "cli.vendor.index_sealed",
            shard=report.manifest.shard_id,
            packages=report.manifest.doc_count,
            chunks=report.manifest.chunk_count,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
