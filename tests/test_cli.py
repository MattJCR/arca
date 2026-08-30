import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from arca import __version__
from arca.catalog import EntryNotFoundError, Resolution, find_entry, parse_entries
from arca.cli import main
from arca.config import ENV_ROOT, config_path, library_db_path
from arca.db import open_library_ro, open_library_rw, recent_events
from arca.fetch import enqueue
from tests.support import Behavior, DownloadServer

FIXTURES = Path(__file__).parent / "fixtures"


def test_version_flag_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_missing_root_fails_with_a_clear_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENV_ROOT, raising=False)
    assert main(["diagnostics"]) == 2
    assert "ARCA_ROOT" in capsys.readouterr().err


def test_missing_root_message_is_localized(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENV_ROOT, raising=False)
    assert main(["--lang", "en", "init"]) == 2
    assert "Missing library path" in capsys.readouterr().err


def test_init_creates_a_working_library(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "library"
    assert main(["--root", str(root), "init"]) == 0
    assert "inicializada" in capsys.readouterr().out
    assert config_path(root).is_file()
    assert library_db_path(root).is_file()
    assert (root / "logs" / "arca.log").exists()
    connection = open_library_ro(library_db_path(root))
    try:
        events = recent_events(connection)
        assert events[0]["area"] == "init"
    finally:
        connection.close()


def test_init_twice_reports_the_existing_library(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "init"]) == 0
    assert "ya estaba" in capsys.readouterr().out


def test_root_can_come_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    monkeypatch.setenv(ENV_ROOT, str(root))
    assert main(["init"]) == 0
    assert config_path(root).is_file()


def test_diagnostics_exit_zero_on_a_healthy_library(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "diagnostics"]) == 0
    output = capsys.readouterr().out
    assert "correcto" in output


def test_diagnostics_exit_nonzero_when_the_root_is_broken(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nowhere"
    assert main(["--root", str(missing), "diagnostics"]) == 1
    output = capsys.readouterr().out
    assert "roto" in output


def test_serve_rejects_ports_outside_the_valid_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "serve", "--port", "99999"]) == 2
    assert "65535" in capsys.readouterr().err


def test_diagnostics_render_in_english_with_lang_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "--lang", "en", "diagnostics"]) == 0
    assert "[ok]" in capsys.readouterr().out


@pytest.fixture
def server() -> Iterator[DownloadServer]:
    instance = DownloadServer()
    instance.start()
    yield instance
    instance.stop()


def _fixture_entries() -> bytes:
    return (FIXTURES / "catalog_entries.xml").read_bytes()


def test_search_lists_catalog_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("arca.cli.fetch_entries", lambda url: parse_entries(_fixture_entries()))
    assert main(["--root", str(tmp_path), "search", "wikipedia", "--lang", "spa"]) == 0
    output = capsys.readouterr().out
    assert "0c45160e-f917-760a-9159-dfe3c53cdcdd" in output
    assert "Wikipedia (Spanish)" in output
    assert "104.5 GiB" in output


def test_search_reports_network_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from arca.catalog import CatalogError

    def failing(url: str) -> list[object]:
        raise CatalogError("offline")

    monkeypatch.setattr("arca.cli.fetch_entries", failing)
    assert main(["--root", str(tmp_path), "search", "wikipedia"]) == 1
    assert "offline" in capsys.readouterr().err


def test_add_queues_the_download_and_records_the_zim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    uuid = "0c45160e-f917-760a-9159-dfe3c53cdcdd"
    entry = find_entry(parse_entries(_fixture_entries()), uuid)
    resolution = Resolution(
        size_bytes=100, sha256="a" * 64, mirrors=("https://m1/x.zim",), hash_missing=False
    )
    monkeypatch.setattr("arca.cli.resolve_entry", lambda requested: (entry, resolution))
    assert main(["--root", str(root), "add", uuid]) == 0
    assert "encolada" in capsys.readouterr().out
    connection = open_library_ro(library_db_path(root))
    try:
        download = connection.execute("SELECT * FROM downloads").fetchone()
        assert download["ref"] == uuid
        assert download["status"] == "queued"
        assert download["sha256"] == "a" * 64
        zim = connection.execute("SELECT * FROM zim_files").fetchone()
        assert zim["uuid"] == uuid
        assert zim["status"] == "missing"
    finally:
        connection.close()


def test_add_reports_unknown_uuids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()

    def not_found(requested: str) -> object:
        raise EntryNotFoundError(requested)

    monkeypatch.setattr("arca.cli.resolve_entry", not_found)
    assert main(["--root", str(root), "add", "unknown-uuid"]) == 1
    assert "unknown-uuid" in capsys.readouterr().err


def test_run_with_an_empty_queue_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "run"]) == 0
    assert "cola" in capsys.readouterr().out


def test_run_downloads_a_queued_file(
    tmp_path: Path, server: DownloadServer, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    payload = b"zim content for the cli run test"
    server.add("/file.zim", Behavior(payload=payload))
    connection = open_library_rw(library_db_path(root))
    try:
        connection.execute(
            "INSERT INTO zim_files (uuid, book_name, filename, status) "
            "VALUES ('u1', 'book', 'file.zim', 'missing')"
        )
        connection.commit()
        enqueue(
            connection,
            kind="zim",
            ref="u1",
            filename="file.zim",
            dest_path=root / "zim" / "file.zim",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            mirrors=(server.url("/file.zim"),),
        )
    finally:
        connection.close()
    assert main(["--root", str(root), "run"]) == 0
    assert "Completada: file.zim" in capsys.readouterr().out
    assert (root / "zim" / "file.zim").read_bytes() == payload


def test_verify_command_reports_an_empty_library(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "verify"]) == 0
    assert "verificar" in capsys.readouterr().out


def test_vendor_verify_reports_missing_pieces_without_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "vendor", "--verify"]) == 1
    assert "AUSENTE" in capsys.readouterr().out


def test_vendor_check_drift_is_clean_on_the_real_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    assert main(["--root", str(root), "vendor", "--check-drift"]) == 0
    assert "alineado" in capsys.readouterr().out


def test_import_stores_documents_and_skips_duplicates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.support import build_pdf

    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    first = build_pdf(tmp_path / "inbox" / "one.pdf", [["A first document."]], title="One")
    build_pdf(tmp_path / "inbox" / "two.pdf", [["A second, different document."]], title="Two")
    code = main(["--root", str(root), "--lang", "en", "import", str(tmp_path / "inbox")])
    output = capsys.readouterr().out
    assert code == 0
    assert output.count("imported") == 2
    assert "2 new" in output
    code = main(["--root", str(root), "--lang", "en", "import", str(first)])
    output = capsys.readouterr().out
    assert code == 0
    assert "already here" in output
    assert "0 new, 1 already present" in output
    assert len(list((root / "docs" / "originals").glob("*.pdf"))) == 2


def test_import_rejects_bad_files_without_stopping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.support import build_pdf

    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    build_pdf(tmp_path / "inbox" / "good.pdf", [["A readable document."]], title="Good")
    broken = tmp_path / "inbox" / "bad.pdf"
    broken.write_bytes(b"%PDF-1.7 not really")
    code = main(["--root", str(root), "--lang", "en", "import", str(tmp_path / "inbox")])
    captured = capsys.readouterr()
    assert code == 1
    assert "REJECTED" in captured.err
    assert "1 new" in captured.out
    assert "1 rejected" in captured.out


def test_import_reports_when_there_is_nothing_to_do(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "library"
    main(["--root", str(root), "init"])
    capsys.readouterr()
    empty = tmp_path / "inbox"
    empty.mkdir()
    assert main(["--root", str(root), "--lang", "en", "import", str(empty)]) == 0
    assert "No document to import" in capsys.readouterr().out
