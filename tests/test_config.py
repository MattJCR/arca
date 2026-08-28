import json
import tempfile
from pathlib import Path

from arca.config import (
    ENV_ROOT,
    LIBRARY_SUBDIRS,
    ArcaConfig,
    config_path,
    init_library,
    load_config,
    resolve_root,
    scan_root,
    session_dir,
    setup_logging,
)


def test_init_library_creates_the_full_structure(tmp_path: Path) -> None:
    root = tmp_path / "library"
    created = init_library(root)
    assert created is True
    assert config_path(root).is_file()
    for sub in LIBRARY_SUBDIRS:
        assert (root / sub).is_dir(), sub


def test_init_library_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    marker = json.loads(config_path(root).read_text("utf-8"))
    marker["language"] = "en"
    config_path(root).write_text(json.dumps(marker), "utf-8")
    assert init_library(root) is False
    assert load_config(root).language == "en"


def test_load_config_defaults_when_file_is_missing(tmp_path: Path) -> None:
    assert load_config(tmp_path) == ArcaConfig()


def test_load_config_defaults_when_file_is_invalid(tmp_path: Path) -> None:
    config_path(tmp_path).write_text("{ not json", "utf-8")
    assert load_config(tmp_path) == ArcaConfig()
    config_path(tmp_path).write_text("[1, 2]", "utf-8")
    assert load_config(tmp_path) == ArcaConfig()


def test_load_config_keeps_valid_values_and_discards_bad_ones(tmp_path: Path) -> None:
    payload = {
        "language": "en",
        "port": "8042",
        "free_space_warn_gib": -3,
        "host": "0.0.0.0",
        "unknown_key": True,
    }
    config_path(tmp_path).write_text(json.dumps(payload), "utf-8")
    loaded = load_config(tmp_path)
    assert loaded.language == "en"
    assert loaded.host == "0.0.0.0"
    assert loaded.port == ArcaConfig().port
    assert loaded.free_space_warn_gib == ArcaConfig().free_space_warn_gib


def test_load_config_normalizes_unsupported_language(tmp_path: Path) -> None:
    config_path(tmp_path).write_text(json.dumps({"language": "fr"}), "utf-8")
    assert load_config(tmp_path).language == "es"


def test_resolve_root_prefers_cli_over_environment(tmp_path: Path) -> None:
    assert resolve_root("X:/cli", {ENV_ROOT: "Y:/env"}) == Path("X:/cli")
    assert resolve_root(None, {ENV_ROOT: "Y:/env"}) == Path("Y:/env")
    assert resolve_root(None, {ENV_ROOT: "   "}) is None
    assert resolve_root(None, {}) is None


def test_session_dir_defaults_outside_the_root(tmp_path: Path) -> None:
    resolved = session_dir(tmp_path, ArcaConfig())
    assert resolved == Path(tempfile.gettempdir()) / "arca"
    assert tmp_path not in resolved.parents


def test_session_dir_rejects_locations_inside_the_root(tmp_path: Path) -> None:
    inside = tmp_path / "cache"
    config = ArcaConfig(session_dir=str(inside))
    assert session_dir(tmp_path, config) == Path(tempfile.gettempdir()) / "arca"
    assert session_dir(tmp_path, ArcaConfig(session_dir="relative/dir")) == (
        Path(tempfile.gettempdir()) / "arca"
    )


def test_session_dir_accepts_absolute_locations_outside_the_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere"
    config = ArcaConfig(session_dir=str(outside))
    assert session_dir(tmp_path / "library", config) == outside


def test_scan_root_reports_missing_root(tmp_path: Path) -> None:
    scan = scan_root(tmp_path / "nowhere")
    assert scan.root_exists is False
    assert scan.zim_count == 0


def test_scan_root_counts_real_content(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    (root / "zim" / "wikipedia_es.zim").write_bytes(b"z")
    (root / "zim" / "wikipedia_es.zim.sha256").write_text("x", "utf-8")
    (root / "models" / "chat" / "chat.gguf").write_bytes(b"g")
    (root / "models" / "embed" / "embed.gguf").write_bytes(b"g")
    scan = scan_root(root)
    assert scan.root_exists and scan.config_present
    assert scan.database_present is False
    assert scan.zim_count == 1
    assert scan.chat_model_count == 1
    assert scan.embed_model_count == 1


def test_setup_logging_writes_to_the_given_directory_once(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path / "logs")
    handlers_before = len(logger.handlers)
    logger = setup_logging(tmp_path / "logs")
    assert len(logger.handlers) == handlers_before
    logger.info("probe")
    for handler in logger.handlers:
        handler.flush()
    assert (tmp_path / "logs" / "arca.log").read_text("utf-8").strip()
