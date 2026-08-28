from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

from arca import __version__
from arca.api import create_app
from arca.catalog import (
    CatalogEntry,
    CatalogError,
    EntryNotFoundError,
    entries_url,
    fetch_entries,
    format_size,
    resolve_entry,
)
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
    ProgressCallback,
    ProgressUpdate,
    queue_zim_download,
    run_queue,
    verify_library,
)
from arca.i18n import Catalog, load_catalog, normalize_language
from arca.ingest import IngestError, receive_document
from arca.pipeline import ingest_vendor
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
    search.add_argument("--name", default="")
    add = subparsers.add_parser("add")
    add.add_argument("uuid")
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
        return _run_search(catalog, args.query, args.filter_lang, args.category, args.name)
    if args.command == "add":
        return _run_add(root, catalog, args.uuid)
    if args.command == "run":
        return _run_downloads(root, config, catalog, args.concurrency)
    if args.command == "verify":
        return _run_verify(root, catalog)
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


def _run_search(catalog: Catalog, query: str, filter_lang: str, category: str, name: str) -> int:
    url = entries_url(query=query, lang=filter_lang, category=category, name=name)
    try:
        entries = fetch_entries(url)
    except CatalogError as error:
        print(catalog.text("cli.search.error", error=str(error)), file=sys.stderr)
        return 1
    if not entries:
        print(catalog.text("cli.search.empty"))
        return 0
    print(catalog.text("cli.search.header", count=len(entries)))
    for entry in entries:
        print(_search_line(catalog, entry))
    return 0


def _search_line(catalog: Catalog, entry: CatalogEntry) -> str:
    articles = str(entry.article_count) if entry.article_count is not None else "?"
    return catalog.text(
        "cli.search.entry",
        uuid=entry.uuid,
        title=entry.title,
        language=entry.language or "?",
        flavour=entry.flavour or "-",
        size=format_size(entry.size_bytes),
        articles=articles,
    )


def _run_add(root: Path, catalog: Catalog, uuid: str) -> int:
    try:
        entry, resolution = resolve_entry(uuid)
    except EntryNotFoundError:
        print(catalog.text("cli.add.not_found", uuid=uuid), file=sys.stderr)
        return 1
    except CatalogError as error:
        print(catalog.text("cli.add.error", error=str(error)), file=sys.stderr)
        return 1
    connection = open_library_rw(library_db_path(root))
    try:
        download_id = queue_zim_download(connection, root, entry, resolution)
    finally:
        connection.close()
    if resolution.hash_missing:
        print(catalog.text("cli.add.no_hash_warning", filename=entry.filename))
    print(catalog.text("cli.add.queued", id=download_id, filename=entry.filename))
    return 0


def _run_downloads(
    root: Path, config: ArcaConfig, catalog: Catalog, concurrency: int | None
) -> int:
    results = run_queue(
        library_db_path(root),
        concurrency=concurrency or config.download_concurrency,
        max_attempts=config.download_max_attempts,
        progress=_progress_printer(catalog),
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


def _run_verify(root: Path, catalog: Catalog) -> int:
    connection = open_library_rw(library_db_path(root))
    try:
        outcomes = verify_library(connection, root)
    finally:
        connection.close()
    if not outcomes:
        print(catalog.text("cli.verify.empty"))
        return 0
    print(catalog.text("cli.verify.header", count=len(outcomes)))
    problems = 0
    for outcome in outcomes:
        print(catalog.text(f"cli.verify.{outcome.result}", filename=outcome.filename))
        if outcome.result in ("corrupt", "missing"):
            problems += 1
    return 1 if problems else 0


def _collect_documents(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            found.extend(sorted(candidate.rglob("*.pdf")))
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
