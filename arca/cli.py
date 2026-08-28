from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from arca import __version__
from arca.api import create_app
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
from arca.i18n import Catalog, load_catalog, normalize_language


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arca")
    parser.add_argument("--version", action="version", version=f"arca {__version__}")
    parser.add_argument("--root")
    parser.add_argument("--lang")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    subparsers.add_parser("diagnostics")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lang_override: str | None = args.lang
    root = resolve_root(args.root, os.environ)
    if root is None:
        catalog = load_catalog(normalize_language(lang_override))
        print(catalog.text("cli.root_missing"), file=sys.stderr)
        return 2
    config = load_config(root)
    language = normalize_language(lang_override) if lang_override else config.language
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


if __name__ == "__main__":
    raise SystemExit(main())
