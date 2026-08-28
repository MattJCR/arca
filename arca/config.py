from __future__ import annotations

import json
import logging
import logging.handlers
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from arca.i18n import DEFAULT_LANGUAGE, normalize_language

CONFIG_FILENAME = "arca.json"
LIBRARY_DB_FILENAME = "library.db"
LOG_FILENAME = "arca.log"
ENV_ROOT = "ARCA_ROOT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8042
DEFAULT_FREE_SPACE_WARN_GIB = 10.0
FREE_SPACE_FLOOR_GIB = 1.0
DEFAULT_DOWNLOAD_CONCURRENCY = 2
DEFAULT_DOWNLOAD_MAX_ATTEMPTS = 5
DEFAULT_EMBED_URL = "http://127.0.0.1:8080"
DEFAULT_EMBED_MODEL = "multilingual-e5-base"
DEFAULT_EMBED_DIMS = 768
DEFAULT_CHAT_URL = "http://127.0.0.1:8081"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 3

LIBRARY_SUBDIRS: tuple[str, ...] = (
    "zim",
    "docs/originals",
    "docs/shards",
    "docs/vendor",
    "models/chat",
    "models/embed",
    "vendor/wheelhouse",
    "vendor/llama.cpp",
    "vendor/docs",
    "logs",
)


@dataclass(frozen=True)
class ArcaConfig:
    language: str = DEFAULT_LANGUAGE
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    free_space_warn_gib: float = DEFAULT_FREE_SPACE_WARN_GIB
    session_dir: str = ""
    download_concurrency: int = DEFAULT_DOWNLOAD_CONCURRENCY
    download_max_attempts: int = DEFAULT_DOWNLOAD_MAX_ATTEMPTS
    embed_url: str = DEFAULT_EMBED_URL
    embed_model: str = DEFAULT_EMBED_MODEL
    embed_dims: int = DEFAULT_EMBED_DIMS
    chat_url: str = DEFAULT_CHAT_URL
    chat_model: str = ""
    llama_bin: str = ""


@dataclass(frozen=True)
class RootScan:
    root_exists: bool
    config_present: bool
    database_present: bool
    zim_count: int
    chat_model_count: int
    embed_model_count: int


def resolve_root(cli_root: str | None, env: Mapping[str, str]) -> Path | None:
    if cli_root:
        return Path(cli_root)
    env_value = env.get(ENV_ROOT, "").strip()
    if env_value:
        return Path(env_value)
    return None


def config_path(root: Path) -> Path:
    return root / CONFIG_FILENAME


def library_db_path(root: Path) -> Path:
    return root / LIBRARY_DB_FILENAME


def log_dir(root: Path) -> Path:
    return root / "logs"


def load_config(root: Path) -> ArcaConfig:
    defaults = ArcaConfig()
    try:
        parsed: object = json.loads(config_path(root).read_text("utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(parsed, dict):
        return defaults
    return ArcaConfig(
        language=normalize_language(_read_str(parsed, "language", defaults.language)),
        host=_read_str(parsed, "host", defaults.host) or defaults.host,
        port=_read_port(parsed, defaults.port),
        free_space_warn_gib=_read_positive_float(
            parsed, "free_space_warn_gib", defaults.free_space_warn_gib
        ),
        session_dir=_read_str(parsed, "session_dir", defaults.session_dir),
        download_concurrency=_read_positive_int(
            parsed, "download_concurrency", defaults.download_concurrency
        ),
        download_max_attempts=_read_positive_int(
            parsed, "download_max_attempts", defaults.download_max_attempts
        ),
        embed_url=_read_str(parsed, "embed_url", defaults.embed_url) or defaults.embed_url,
        embed_model=_read_str(parsed, "embed_model", defaults.embed_model) or defaults.embed_model,
        embed_dims=_read_positive_int(parsed, "embed_dims", defaults.embed_dims),
        chat_url=_read_str(parsed, "chat_url", defaults.chat_url) or defaults.chat_url,
        chat_model=_read_str(parsed, "chat_model", defaults.chat_model),
        llama_bin=_read_str(parsed, "llama_bin", defaults.llama_bin),
    )


def init_library(root: Path) -> bool:
    root.mkdir(parents=True, exist_ok=True)
    for sub in LIBRARY_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    if config_path(root).exists():
        return False
    payload = json.dumps(asdict(ArcaConfig()), indent=2, ensure_ascii=False)
    config_path(root).write_text(payload + "\n", "utf-8")
    return True


def session_dir(root: Path, config: ArcaConfig) -> Path:
    fallback = Path(tempfile.gettempdir()) / "arca"
    if not config.session_dir:
        return fallback
    candidate = Path(config.session_dir)
    if not candidate.is_absolute():
        return fallback
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate == resolved_root or resolved_root in resolved_candidate.parents:
        return fallback
    return candidate


def scan_root(root: Path) -> RootScan:
    if not root.is_dir():
        return RootScan(False, False, False, 0, 0, 0)
    return RootScan(
        root_exists=True,
        config_present=config_path(root).is_file(),
        database_present=library_db_path(root).is_file(),
        zim_count=_count_files(root / "zim", "*.zim"),
        chat_model_count=_count_files(root / "models" / "chat", "*.gguf"),
        embed_model_count=_count_files(root / "models" / "embed", "*.gguf"),
    )


def setup_logging(directory: Path) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("arca")
    logger.setLevel(logging.INFO)
    target = directory / LOG_FILENAME
    already_attached = any(
        isinstance(handler, logging.handlers.RotatingFileHandler)
        and Path(handler.baseFilename) == target
        for handler in logger.handlers
    )
    if not already_attached:
        handler = logging.handlers.RotatingFileHandler(
            target, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _read_str(parsed: dict[object, object], key: str, default: str) -> str:
    value = parsed.get(key)
    if isinstance(value, str):
        return value
    return default


def _read_port(parsed: dict[object, object], default: int) -> int:
    value = parsed.get("port")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    return default


def _read_positive_int(parsed: dict[object, object], key: str, default: int) -> int:
    value = parsed.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _read_positive_float(parsed: dict[object, object], key: str, default: float) -> float:
    value = parsed.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default


def _count_files(directory: Path, pattern: str) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for entry in directory.glob(pattern) if entry.is_file())
