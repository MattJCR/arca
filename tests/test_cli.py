from pathlib import Path

import pytest

from arca import __version__
from arca.cli import main
from arca.config import ENV_ROOT, config_path, library_db_path
from arca.db import open_library_ro, recent_events


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
