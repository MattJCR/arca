from __future__ import annotations

import ctypes
import json
import os
import re
import shlex
import shutil
import sqlite3
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

GGUF_MAGIC = b"GGUF"
GGUF_MAX_VERSION = 3
MODELS_CHAT = ("models", "chat")
MODELS_EMBED = ("models", "embed")
MAX_STRING_BYTES = 65536
MAX_KEY_VALUES = 4096
INTERESTING_KEYS = (
    "general.architecture",
    "general.name",
    "general.file_type",
    "general.quantization_version",
)
CONTEXT_SUFFIX = ".context_length"
EMBEDDING_SUFFIX = ".embedding_length"
BLOCK_SUFFIX = ".block_count"

TYPE_UINT8 = 0
TYPE_INT8 = 1
TYPE_UINT16 = 2
TYPE_INT16 = 3
TYPE_UINT32 = 4
TYPE_INT32 = 5
TYPE_FLOAT32 = 6
TYPE_BOOL = 7
TYPE_STRING = 8
TYPE_ARRAY = 9
TYPE_UINT64 = 10
TYPE_INT64 = 11
TYPE_FLOAT64 = 12

SCALARS = {
    TYPE_UINT8: ("<B", 1),
    TYPE_INT8: ("<b", 1),
    TYPE_UINT16: ("<H", 2),
    TYPE_INT16: ("<h", 2),
    TYPE_UINT32: ("<I", 4),
    TYPE_INT32: ("<i", 4),
    TYPE_FLOAT32: ("<f", 4),
    TYPE_BOOL: ("<?", 1),
    TYPE_UINT64: ("<Q", 8),
    TYPE_INT64: ("<q", 8),
    TYPE_FLOAT64: ("<d", 8),
}

FILE_TYPES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    7: "Q8_0",
    8: "Q5_0",
    9: "Q5_1",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS",
    20: "IQ2_XS",
    30: "BF16",
}


BINARY_NAMES = ("llama-server", "llama-server.exe")
VENDOR_SUBDIR = ("vendor", "llama.cpp")
SOURCE_VENDOR = "vendor"
SOURCE_PATH = "path"
SOURCE_CONFIGURED = "configured"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
LOOPBACK_HOSTS = ("0.0.0.0", "::", "*")
HEALTH_PATH = "/health"
HEALTH_OK = "ok"
HEALTH_LOADING = "loading"
HEALTH_DOWN = "down"
HEALTH_TIMEOUT_SECONDS = 3.0
PROBE_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.5
LOG_CAPACITY = 400
GPU_MARKERS = ("cuda", "rocm", "hip", "vulkan", "metal", "sycl", "musa", "cann")
CPU_DEVICE_PREFIXES = ("cpu", "none")
GIB = 1024**3
CHAT_PATH = "/v1/chat/completions"
HEALTH_WAIT_SECONDS = 60.0
GENERATION_TIMEOUT_SECONDS = 120.0
FINISH_LENGTH = "length"
TEST_MESSAGES: tuple[dict[str, str], ...] = (
    {"role": "user", "content": "Reply with one word: ready"},
)

SOURCE_BINARY = "binary"
SOURCE_CAPTURE = "capture"
HELP_CAPTURE_FILENAME = "llama-server-help.txt"
EMBEDDING_POOLING = "mean"
EMBEDDING_CONTEXT = "512"
PROFILE_NAME_PATTERN = re.compile(r"^[\w][\w .-]*$")
MODE_FORM = "form"
MODE_RAW = "raw"
PROFILE_MODES = (MODE_FORM, MODE_RAW)
PROFILE_COLUMNS = "name, args, raw_cmd, mode, is_default, last_good, created_at"

STAGE_START = "start"
STAGE_HEALTH = "health"
STAGE_GENERATION = "generation"
STAGE_OK = "ok"

GROUP_MODEL = "model"
GROUP_CONTEXT = "context"
GROUP_COMPUTE = "compute"
GROUP_GPU = "gpu"
GROUP_MEMORY = "memory"
GROUP_ROPE = "rope"
GROUP_CONCURRENCY = "concurrency"
GROUP_TEMPLATE = "template"
GROUP_SERVER = "server"
GROUP_EMBEDDINGS = "embeddings"
GROUP_LOGGING = "logging"
GROUP_SAMPLING = "sampling"
GROUP_OTHER = "other"
GROUP_ORDER = (
    GROUP_MODEL,
    GROUP_CONTEXT,
    GROUP_COMPUTE,
    GROUP_GPU,
    GROUP_MEMORY,
    GROUP_ROPE,
    GROUP_CONCURRENCY,
    GROUP_TEMPLATE,
    GROUP_SERVER,
    GROUP_EMBEDDINGS,
    GROUP_LOGGING,
    GROUP_SAMPLING,
    GROUP_OTHER,
)
CURATED_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        GROUP_MODEL,
        ("--model", "--alias", "--mmproj", "--lora", "--lora-scaled", "--control-vector"),
    ),
    (
        GROUP_CONTEXT,
        (
            "--ctx-size",
            "--n-predict",
            "--keep",
            "--cache-type-k",
            "--cache-type-v",
            "--defrag-thold",
            "--no-context-shift",
            "--cache-reuse",
        ),
    ),
    (
        GROUP_COMPUTE,
        ("--threads", "--threads-batch", "--batch-size", "--ubatch-size", "--flash-attn"),
    ),
    (
        GROUP_GPU,
        ("--n-gpu-layers", "--split-mode", "--tensor-split", "--main-gpu", "--device"),
    ),
    (GROUP_MEMORY, ("--mlock", "--no-mmap", "--numa")),
    (GROUP_ROPE, ("--rope-scaling", "--rope-freq-base", "--rope-freq-scale")),
    (GROUP_CONCURRENCY, ("--parallel", "--cont-batching", "--threads-http")),
    (GROUP_TEMPLATE, ("--chat-template", "--chat-template-file", "--jinja")),
    (
        GROUP_SERVER,
        (
            "--host",
            "--port",
            "--path",
            "--timeout",
            "--metrics",
            "--slots",
            "--props",
            "--no-webui",
        ),
    ),
    (GROUP_EMBEDDINGS, ("--embeddings", "--pooling")),
    (GROUP_LOGGING, ("--verbose", "--log-file")),
    (
        GROUP_SAMPLING,
        (
            "--temp",
            "--top-k",
            "--top-p",
            "--min-p",
            "--typical",
            "--repeat-penalty",
            "--repeat-last-n",
            "--presence-penalty",
            "--frequency-penalty",
            "--dry-multiplier",
            "--dry-base",
            "--dry-allowed-length",
            "--dry-penalty-last-n",
            "--dry-sequence-breaker",
            "--xtc-probability",
            "--xtc-threshold",
            "--mirostat",
            "--mirostat-lr",
            "--mirostat-ent",
            "--seed",
            "--grammar",
            "--grammar-file",
            "--json-schema",
            "--samplers",
            "--sampling-seq",
            "--ignore-eos",
            "--logit-bias",
        ),
    ),
)
YARN_PREFIX = "--yarn-"
OPTION_INDENT_LIMIT = 8
SECTION_PATTERN = re.compile(r"^-{3,}\s*(.+?)\s*-{3,}$")
VALUE_PATTERN = re.compile(r"^(\{[^}]*\}|[A-Z][A-Z0-9_,.\[\]/-]*)$")
DEFAULT_PATTERN = re.compile(r"\(default:\s*(.+?)\)")
ENV_PATTERN = re.compile(r"\(env:\s*([A-Z0-9_]+)\)")

MODEL_FLAGS = ("--model", "-m")
CTX_FLAGS = ("--ctx-size", "-c")
NGL_FLAGS = ("--n-gpu-layers", "--gpu-layers", "-ngl")
CACHE_K_FLAGS = ("--cache-type-k", "-ctk")
CACHE_V_FLAGS = ("--cache-type-v", "-ctv")
CACHE_TYPE_BYTES = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 1.0,
    "q5_1": 0.6875,
    "q5_0": 0.6875,
    "q4_1": 0.5625,
    "q4_0": 0.5625,
}
MIB_PATTERN = re.compile(r"(\d+)\s*MiB")

NOTE_CONTEXT_OVER_TRAINED = "context_over_trained"
NOTE_MEMORY_OVER_AVAILABLE = "memory_over_available"
NOTE_GPU_LAYERS_OVER_BLOCKS = "gpu_layers_over_blocks"
NOTE_GPU_LAYERS_WITHOUT_GPU = "gpu_layers_without_gpu"
NOTE_MODEL_MISSING = "model_missing"


class GgufError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelFile:
    path: Path
    name: str
    kind: str
    size_bytes: int
    architecture: str
    quantization: str
    context_length: int
    embedding_length: int
    block_count: int
    tensor_count: int
    error: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


def models_dir(root: Path, kind: str) -> Path:
    parts = MODELS_EMBED if kind == "embed" else MODELS_CHAT
    return root.joinpath(*parts)


def list_models(root: Path) -> list[ModelFile]:
    found: list[ModelFile] = []
    for kind in ("chat", "embed"):
        directory = models_dir(root, kind)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.gguf")):
            found.append(read_model(path, kind))
    return found


def read_model(path: Path, kind: str) -> ModelFile:
    size = _size(path)
    try:
        header = read_gguf_header(path)
    except (GgufError, OSError) as error:
        return ModelFile(path, path.name, kind, size, "", "", 0, 0, 0, 0, str(error))
    architecture = str(header.get("general.architecture", ""))
    return ModelFile(
        path=path,
        name=path.name,
        kind=kind,
        size_bytes=size,
        architecture=architecture,
        quantization=_quantization(header),
        context_length=_as_int(header.get(f"{architecture}{CONTEXT_SUFFIX}")),
        embedding_length=_as_int(header.get(f"{architecture}{EMBEDDING_SUFFIX}")),
        block_count=_as_int(header.get(f"{architecture}{BLOCK_SUFFIX}")),
        tensor_count=_as_int(header.get("__tensor_count__")),
        metadata={key: str(header[key]) for key in INTERESTING_KEYS if header.get(key) is not None},
    )


def read_gguf_header(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        if handle.read(4) != GGUF_MAGIC:
            raise GgufError(f"{path.name} is not a gguf file")
        version = _scalar(handle, TYPE_UINT32)
        if not isinstance(version, int) or not 1 <= version <= GGUF_MAX_VERSION:
            raise GgufError(f"{path.name} declares an unsupported gguf version: {version}")
        tensor_count = _scalar(handle, TYPE_UINT64)
        pairs = _scalar(handle, TYPE_UINT64)
        if not isinstance(pairs, int) or pairs > MAX_KEY_VALUES:
            raise GgufError(f"{path.name} declares an implausible metadata count: {pairs}")
        header: dict[str, object] = {
            "__version__": version,
            "__tensor_count__": tensor_count,
        }
        for _ in range(pairs):
            key = _string(handle)
            value_type = int(_scalar(handle, TYPE_UINT32))
            header[key] = _value(handle, value_type)
        return header


def _value(handle: BinaryIO, value_type: int) -> object:
    if value_type == TYPE_STRING:
        return _string(handle)
    if value_type == TYPE_ARRAY:
        item_type = int(_scalar(handle, TYPE_UINT32))
        length = int(_scalar(handle, TYPE_UINT64))
        if length > MAX_KEY_VALUES:
            _skip_array(handle, item_type, length)
            return f"[{length} items]"
        return [_value(handle, item_type) for _ in range(length)]
    return _scalar(handle, value_type)


def _skip_array(handle: BinaryIO, item_type: int, length: int) -> None:
    if item_type in SCALARS:
        _, size = SCALARS[item_type]
        handle.seek(size * length, 1)
        return
    for _ in range(length):
        _value(handle, item_type)


def _scalar(handle: BinaryIO, value_type: int) -> int | float | bool:
    layout = SCALARS.get(value_type)
    if layout is None:
        raise GgufError(f"unsupported gguf value type: {value_type}")
    fmt, size = layout
    raw = handle.read(size)
    if len(raw) != size:
        raise GgufError("gguf header ends before its declared metadata")
    unpacked: int | float | bool = struct.unpack(fmt, raw)[0]
    return unpacked


def _string(handle: BinaryIO) -> str:
    length = int(_scalar(handle, TYPE_UINT64))
    if length > MAX_STRING_BYTES:
        raise GgufError(f"gguf header declares an implausible string of {length} bytes")
    raw = handle.read(length)
    if len(raw) != length:
        raise GgufError("gguf header ends inside a string")
    return raw.decode("utf-8", "replace")


def _quantization(header: dict[str, object]) -> str:
    value = header.get("general.file_type")
    if isinstance(value, int):
        return FILE_TYPES.get(value, f"type {value}")
    return ""


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


@dataclass(frozen=True)
class BinaryInfo:
    path: Path | None
    source: str = ""
    version: str = ""
    devices: tuple[str, ...] = ()
    gpu: bool = False
    error: str = ""

    @property
    def present(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class ServerStatus:
    running: bool
    pid: int
    command: tuple[str, ...]
    base_url: str
    health: str
    exit_code: int | None
    started_at: str
    error: str

    @property
    def command_line(self) -> str:
        return format_command(self.command)


def find_binary(root: Path, configured: str = "") -> tuple[Path | None, str]:
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return candidate, SOURCE_CONFIGURED
        located = shutil.which(configured)
        if located:
            return Path(located), SOURCE_CONFIGURED
        return None, ""
    vendor = root.joinpath(*VENDOR_SUBDIR)
    if vendor.is_dir():
        for name in BINARY_NAMES:
            for found in sorted(vendor.rglob(name)):
                if found.is_file():
                    return found, SOURCE_VENDOR
    for name in BINARY_NAMES:
        located = shutil.which(name)
        if located:
            return Path(located), SOURCE_PATH
    return None, ""


def inspect_binary(root: Path, configured: str = "") -> BinaryInfo:
    path, source = find_binary(root, configured)
    if path is None:
        return BinaryInfo(None)
    version_output, version_error = run_probe([str(path), "--version"])
    devices_output, devices_error = run_probe([str(path), "--list-devices"])
    devices = parse_devices(devices_output)
    return BinaryInfo(
        path=path,
        source=source,
        version=version_line(version_output),
        devices=devices,
        gpu=has_gpu(devices, f"{version_output}\n{devices_output}"),
        error=version_error or devices_error,
    )


def run_probe(command: Sequence[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[str, str]:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return "", str(error)
    return f"{completed.stdout}{completed.stderr}", ""


def version_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("version"):
            return stripped
    for line in output.splitlines():
        if line.strip():
            return line.strip()
    return ""


def parse_devices(output: str) -> tuple[str, ...]:
    devices: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or not line.startswith((" ", "\t")) or ":" not in stripped:
            continue
        devices.append(stripped)
    return tuple(devices)


def has_gpu(devices: Sequence[str], output: str) -> bool:
    for device in devices:
        name = device.split(":", 1)[0].strip().lower()
        if name and not name.startswith(CPU_DEVICE_PREFIXES):
            return True
    lowered = output.lower()
    return any(marker in lowered for marker in GPU_MARKERS)


def format_command(command: Sequence[str]) -> str:
    parts: list[str] = []
    for item in command:
        text = str(item)
        parts.append(f'"{text}"' if not text or " " in text else text)
    return " ".join(parts)


def health_of(base_url: str, timeout: float = HEALTH_TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(f"{base_url.rstrip('/')}{HEALTH_PATH}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HEALTH_OK if response.status == 200 else HEALTH_LOADING
    except urllib.error.HTTPError as error:
        return HEALTH_LOADING if error.code == 503 else HEALTH_DOWN
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return HEALTH_DOWN


def base_url_for(command: Sequence[str], default: str = DEFAULT_BASE_URL) -> str:
    if not command:
        return default
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    values = _flag_values(command, ("--host", "--port"))
    if values.get("--host"):
        host = values["--host"]
    raw_port = values.get("--port", "")
    if raw_port.isdigit():
        port = int(raw_port)
    if host in LOOPBACK_HOSTS:
        host = DEFAULT_HOST
    return f"http://{host}:{port}"


def _flag_values(command: Sequence[str], flags: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    items = [str(item) for item in command]
    for position, item in enumerate(items):
        name, _, inline = item.partition("=")
        if name not in flags:
            continue
        if inline:
            found[name] = inline
        elif position + 1 < len(items):
            found[name] = items[position + 1]
    return found


class LlamaServer:
    def __init__(self, default_url: str = DEFAULT_BASE_URL, capacity: int = LOG_CAPACITY) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._log: deque[str] = deque(maxlen=capacity)
        self._command: tuple[str, ...] = ()
        self._default_url = default_url
        self._started_at = ""
        self._exit_code: int | None = None
        self._error = ""

    @property
    def base_url(self) -> str:
        with self._lock:
            return base_url_for(self._command, self._default_url)

    def start(self, command: Sequence[str], cwd: Path | None = None) -> ServerStatus:
        with self._lock:
            self.stop()
            argv = [str(item) for item in command]
            self._command = tuple(argv)
            self._error = ""
            self._exit_code = None
            self._log.clear()
            if not argv:
                self._error = "empty command"
                return self.status()
            try:
                process = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=str(cwd) if cwd else None,
                )
            except (OSError, ValueError) as error:
                self._error = str(error)
                self._process = None
                return self.status()
            self._process = process
            self._started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._reader = threading.Thread(target=self._drain, args=(process,), daemon=True)
            self._reader.start()
            return self.status()

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> ServerStatus:
        with self._lock:
            process = self._process
            reader = self._reader
            self._process = None
            self._reader = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=timeout)
            with self._lock:
                self._exit_code = process.returncode
        if reader is not None:
            reader.join(timeout=timeout)
        return self.status()

    def restart(self, cwd: Path | None = None) -> ServerStatus:
        with self._lock:
            command = self._command
        if not command:
            return self.status()
        return self.start(command, cwd=cwd)

    def status(self) -> ServerStatus:
        with self._lock:
            process = self._process
            running = process is not None and process.poll() is None
            if process is not None and not running:
                self._exit_code = process.returncode
            return ServerStatus(
                running=running,
                pid=process.pid if process is not None and running else 0,
                command=self._command,
                base_url=base_url_for(self._command, self._default_url),
                health=HEALTH_DOWN,
                exit_code=self._exit_code,
                started_at=self._started_at if running else "",
                error=self._error,
            )

    def probe(self, timeout: float = HEALTH_TIMEOUT_SECONDS) -> ServerStatus:
        status = self.status()
        health = self.health(timeout=timeout)
        return ServerStatus(
            running=status.running,
            pid=status.pid,
            command=status.command,
            base_url=status.base_url,
            health=health,
            exit_code=status.exit_code,
            started_at=status.started_at,
            error=status.error,
        )

    def health(self, timeout: float = HEALTH_TIMEOUT_SECONDS) -> str:
        return health_of(self.base_url, timeout=timeout)

    def wait_healthy(self, timeout: float, interval: float = POLL_INTERVAL_SECONDS) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if self.health(timeout=min(interval * 4, timeout)) == HEALTH_OK:
                return True
            with self._lock:
                process = self._process
            if process is None or process.poll() is not None:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    def log(self, limit: int = 200) -> list[str]:
        with self._lock:
            lines = list(self._log)
        return lines[-limit:] if limit > 0 else lines

    def _drain(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is None:
            return
        for line in stream:
            with self._lock:
                self._log.append(line.rstrip("\r\n"))
        stream.close()


@dataclass(frozen=True)
class HelpOption:
    flag: str
    aliases: tuple[str, ...]
    value: str
    description: str
    section: str
    group: str
    default: str
    env: str

    @property
    def boolean(self) -> bool:
        return not self.value


def parse_help(text: str) -> list[HelpOption]:
    options: list[HelpOption] = []
    section = ""
    pending: list[str] = []
    current: tuple[str, tuple[str, ...], str, str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        heading = SECTION_PATTERN.match(stripped)
        if heading:
            _flush_option(options, current, pending)
            current = None
            section = heading.group(1)
            continue
        if _is_option_line(line):
            _flush_option(options, current, pending)
            current = None
            parsed = _split_option(stripped)
            if parsed is None:
                continue
            flags, value, description = parsed
            current = (_canonical(flags), _aliases(flags), value, section)
            pending.append(description)
            continue
        if current is not None:
            pending.append(stripped)
    _flush_option(options, current, pending)
    return options


def supported_flags(text: str) -> frozenset[str]:
    options = parse_help(text)
    return frozenset(name for option in options for name in (option.flag, *option.aliases) if name)


def curated_group(flags: Sequence[str]) -> str:
    for flag in flags:
        if flag.startswith(YARN_PREFIX):
            return GROUP_ROPE
        for group, members in CURATED_GROUPS:
            if flag in members:
                return group
    return GROUP_OTHER


def grouped_help(options: Sequence[HelpOption]) -> list[tuple[str, list[HelpOption]]]:
    buckets: dict[str, list[HelpOption]] = {group: [] for group in GROUP_ORDER}
    for option in options:
        buckets.setdefault(option.group, []).append(option)
    return [(group, buckets[group]) for group in GROUP_ORDER if buckets.get(group)]


def read_help(command: Sequence[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[str, str]:
    return run_probe([*command, "--help"], timeout=timeout)


def help_source(root: Path, binary: Path | None) -> tuple[str, str, str]:
    if binary is not None:
        text, error = read_help([str(binary)])
        if text.strip():
            return text, SOURCE_BINARY, ""
        return "", "", error or "the binary printed no help"
    captured = root.joinpath(*VENDOR_SUBDIR) / HELP_CAPTURE_FILENAME
    if captured.is_file():
        try:
            return captured.read_text("utf-8"), SOURCE_CAPTURE, ""
        except OSError as error:
            return "", "", str(error)
    return "", "", ""


def is_profile_name(name: str) -> bool:
    return bool(name) and len(name) <= 64 and bool(PROFILE_NAME_PATTERN.match(name))


def model_file(root: Path, kind: str, name: str = "") -> ModelFile | None:
    directory = models_dir(root, kind)
    if name:
        path = directory / Path(name).name
        return read_model(path, kind) if path.is_file() else None
    models = [model for model in list_models(root) if model.kind == kind and not model.error]
    return models[0] if models else None


def chat_model_file(root: Path, name: str = "") -> ModelFile | None:
    return model_file(root, "chat", name)


def embed_model_file(root: Path, name: str = "") -> ModelFile | None:
    return model_file(root, "embed", name)


def embedding_command(binary: Path, model: ModelFile, base_url: str) -> list[str]:
    parts = urllib.parse.urlsplit(base_url)
    return [
        str(binary),
        "--model",
        str(model.path),
        "--embeddings",
        "--pooling",
        EMBEDDING_POOLING,
        "--ctx-size",
        EMBEDDING_CONTEXT,
        "--ubatch-size",
        EMBEDDING_CONTEXT,
        "--host",
        parts.hostname or DEFAULT_HOST,
        "--port",
        str(parts.port or DEFAULT_PORT),
    ]


def referenced_model(config_name: str, profile: Profile | None) -> str:
    if config_name:
        return Path(config_name).name
    if profile is None:
        return ""
    return Path(argv_value(profile_argv(profile), MODEL_FLAGS)).name


def _is_option_line(line: str) -> bool:
    stripped = line.lstrip()
    indent = len(line) - len(stripped)
    return indent < OPTION_INDENT_LIMIT and stripped.startswith("-")


def _split_option(text: str) -> tuple[list[str], str, str] | None:
    tokens = text.split()
    flags: list[str] = []
    position = 0
    while position < len(tokens):
        token = tokens[position]
        name = token.rstrip(",")
        if not name.startswith("-"):
            break
        flags.append(name)
        position += 1
        if not token.endswith(","):
            break
    if not flags:
        return None
    value = ""
    if position < len(tokens) and VALUE_PATTERN.match(tokens[position]):
        value = tokens[position]
        position += 1
    return flags, value, " ".join(tokens[position:])


def _canonical(flags: Sequence[str]) -> str:
    longs = [flag for flag in flags if flag.startswith("--")]
    for flag in longs:
        for _, members in CURATED_GROUPS:
            if flag in members:
                return flag
    if longs:
        return longs[-1]
    return flags[0]


def _aliases(flags: Sequence[str]) -> tuple[str, ...]:
    canonical = _canonical(flags)
    return tuple(flag for flag in flags if flag != canonical)


def _flush_option(
    options: list[HelpOption],
    current: tuple[str, tuple[str, ...], str, str] | None,
    pending: list[str],
) -> None:
    if current is None:
        pending.clear()
        return None
    flag, aliases, value, section = current
    description = " ".join(part for part in pending if part).strip()
    pending.clear()
    default_match = DEFAULT_PATTERN.search(description)
    env_match = ENV_PATTERN.search(description)
    options.append(
        HelpOption(
            flag=flag,
            aliases=aliases,
            value=value,
            description=description,
            section=section,
            group=curated_group([flag, *aliases]),
            default=default_match.group(1).strip("`") if default_match else "",
            env=env_match.group(1) if env_match else "",
        )
    )
    return None


@dataclass(frozen=True)
class Profile:
    name: str
    mode: str = MODE_FORM
    args: dict[str, str] = field(default_factory=dict)
    raw_cmd: str = ""
    is_default: bool = False
    last_good: bool = False
    created_at: str = ""


def profile_argv(profile: Profile) -> list[str]:
    if profile.mode == MODE_RAW:
        return split_raw(profile.raw_cmd)
    argv: list[str] = []
    for flag, value in profile.args.items():
        if not flag.startswith("-"):
            continue
        argv.append(flag)
        if value:
            argv.append(value)
    return argv


def effective_command(binary: Path, profile: Profile) -> list[str]:
    return [str(binary), *profile_argv(profile)]


def split_raw(text: str) -> list[str]:
    try:
        tokens = shlex.split(text, posix=False)
    except ValueError:
        return []
    return [token.strip('"') for token in tokens if token.strip('"')]


def list_profiles(connection: sqlite3.Connection) -> list[Profile]:
    rows = connection.execute(
        f"SELECT {PROFILE_COLUMNS} FROM llama_profiles ORDER BY name"
    ).fetchall()
    return [_profile(row) for row in rows]


def read_profile(connection: sqlite3.Connection, name: str) -> Profile | None:
    row = connection.execute(
        f"SELECT {PROFILE_COLUMNS} FROM llama_profiles WHERE name = ?", (name,)
    ).fetchone()
    return _profile(row) if row is not None else None


def save_profile(connection: sqlite3.Connection, profile: Profile) -> Profile:
    connection.execute(
        "INSERT INTO llama_profiles (name, args, raw_cmd, mode, is_default, last_good, created_at) "
        "VALUES (?, ?, ?, ?, 0, 0, ?) "
        "ON CONFLICT(name) DO UPDATE SET args = excluded.args, raw_cmd = excluded.raw_cmd, "
        "mode = excluded.mode",
        (
            profile.name,
            json.dumps(profile.args, ensure_ascii=False),
            profile.raw_cmd,
            profile.mode if profile.mode in PROFILE_MODES else MODE_FORM,
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
    )
    connection.commit()
    stored = read_profile(connection, profile.name)
    return stored if stored is not None else profile


def delete_profile(connection: sqlite3.Connection, name: str) -> bool:
    cursor = connection.execute("DELETE FROM llama_profiles WHERE name = ?", (name,))
    connection.commit()
    return cursor.rowcount > 0


def set_default_profile(connection: sqlite3.Connection, name: str) -> None:
    connection.execute("UPDATE llama_profiles SET is_default = 0")
    connection.execute("UPDATE llama_profiles SET is_default = 1 WHERE name = ?", (name,))
    connection.commit()


def mark_last_good(connection: sqlite3.Connection, name: str) -> None:
    connection.execute("UPDATE llama_profiles SET last_good = 0")
    connection.execute("UPDATE llama_profiles SET last_good = 1 WHERE name = ?", (name,))
    connection.commit()


def default_profile(connection: sqlite3.Connection) -> Profile | None:
    return _one(connection, "is_default = 1")


def last_good_profile(connection: sqlite3.Connection) -> Profile | None:
    return _one(connection, "last_good = 1")


def _one(connection: sqlite3.Connection, condition: str) -> Profile | None:
    row = connection.execute(
        f"SELECT {PROFILE_COLUMNS} FROM llama_profiles WHERE {condition} LIMIT 1"
    ).fetchone()
    return _profile(row) if row is not None else None


def _profile(row: sqlite3.Row) -> Profile:
    return Profile(
        name=str(row["name"]),
        mode=str(row["mode"] or MODE_FORM),
        args=_args(str(row["args"] or "{}")),
        raw_cmd=str(row["raw_cmd"] or ""),
        is_default=bool(row["is_default"]),
        last_good=bool(row["last_good"]),
        created_at=str(row["created_at"] or ""),
    )


def _args(raw: str) -> dict[str, str]:
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


@dataclass(frozen=True)
class PreflightNote:
    code: str
    params: dict[str, str]


def argv_value(argv: Sequence[str], names: Sequence[str], default: str = "") -> str:
    values = _flag_values(argv, names)
    for name in names:
        if values.get(name):
            return values[name]
    return default


def preflight(argv: Sequence[str], binary: BinaryInfo) -> list[PreflightNote]:
    notes: list[PreflightNote] = []
    model = _model_for(argv, notes)
    context = _as_positive(argv_value(argv, CTX_FLAGS))
    layers = _as_positive(argv_value(argv, NGL_FLAGS))
    if model is not None and context and model.context_length and context > model.context_length:
        notes.append(
            PreflightNote(
                NOTE_CONTEXT_OVER_TRAINED,
                {"requested": str(context), "trained": str(model.context_length)},
            )
        )
    if model is not None and model.block_count and layers > model.block_count:
        notes.append(
            PreflightNote(
                NOTE_GPU_LAYERS_OVER_BLOCKS,
                {"layers": str(layers), "blocks": str(model.block_count)},
            )
        )
    if layers > 0 and binary.present and not binary.gpu:
        notes.append(PreflightNote(NOTE_GPU_LAYERS_WITHOUT_GPU, {"layers": str(layers)}))
    notes.extend(_memory_notes(model, argv, binary, context, layers))
    return notes


def estimate_memory(model: ModelFile, context: int, cache_k: str, cache_v: str) -> int:
    span = context or model.context_length
    if not span or not model.block_count or not model.embedding_length:
        return model.size_bytes
    per_element = CACHE_TYPE_BYTES.get(cache_k, 2.0) + CACHE_TYPE_BYTES.get(cache_v, 2.0)
    cache = model.block_count * span * model.embedding_length * per_element
    return model.size_bytes + int(cache)


def device_memory_bytes(devices: Sequence[str]) -> int:
    for device in devices:
        name = device.split(":", 1)[0].strip().lower()
        if name.startswith(CPU_DEVICE_PREFIXES):
            continue
        amounts = MIB_PATTERN.findall(device)
        if amounts:
            return int(amounts[0]) * 1024 * 1024
    return 0


def system_memory_bytes() -> int:
    if os.name == "nt":
        return _windows_memory_bytes()
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:
        return 0
    try:
        return int(sysconf("SC_PAGE_SIZE")) * int(sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError):
        return 0


class _MemoryStatus(ctypes.Structure):
    _fields_ = (
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    )


def _windows_memory_bytes() -> int:
    loader = getattr(ctypes, "windll", None)
    if loader is None:
        return 0
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    try:
        if not loader.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0
    except (OSError, AttributeError):
        return 0
    return int(status.ullTotalPhys)


def _model_for(argv: Sequence[str], notes: list[PreflightNote]) -> ModelFile | None:
    reference = argv_value(argv, MODEL_FLAGS)
    if not reference:
        return None
    path = Path(reference)
    if not path.is_file():
        notes.append(PreflightNote(NOTE_MODEL_MISSING, {"path": reference}))
        return None
    model = read_model(path, "chat")
    return None if model.error else model


def _memory_notes(
    model: ModelFile | None,
    argv: Sequence[str],
    binary: BinaryInfo,
    context: int,
    layers: int,
) -> list[PreflightNote]:
    if model is None:
        return []
    needed = estimate_memory(
        model,
        context,
        argv_value(argv, ("--cache-type-k", "-ctk"), "f16"),
        argv_value(argv, ("--cache-type-v", "-ctv"), "f16"),
    )
    on_gpu = layers > 0 and binary.gpu
    available = device_memory_bytes(binary.devices) if on_gpu else system_memory_bytes()
    if not available or needed <= available:
        return []
    return [
        PreflightNote(
            NOTE_MEMORY_OVER_AVAILABLE,
            {
                "needed_gib": f"{needed / GIB:.1f}",
                "available_gib": f"{available / GIB:.1f}",
                "target": "vram" if on_gpu else "ram",
            },
        )
    ]


def _as_positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        return 0
    return number if number > 0 else 0


@dataclass(frozen=True)
class Completion:
    text: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""
    reasoning: str = ""

    @property
    def out_of_room(self) -> bool:
        return self.finish_reason == FINISH_LENGTH and not self.text.strip()


def chat_completion(
    base_url: str,
    messages: Sequence[Mapping[str, str]],
    sampling: Mapping[str, object] | None = None,
    timeout: float = GENERATION_TIMEOUT_SECONDS,
) -> Completion:
    payload: dict[str, object] = {"messages": [dict(message) for message in messages]}
    payload.update(dict(sampling or {}))
    payload["stream"] = False
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{CHAT_PATH}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw: bytes = response.read()
    except urllib.error.HTTPError as error:
        return Completion(error=f"llama-server returned HTTP {error.code}")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        return Completion(error=f"llama-server unreachable: {error}")
    return _completion(raw)


def _completion(raw: bytes) -> Completion:
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return Completion(error="llama-server returned invalid json")
    if not isinstance(parsed, dict):
        return Completion(error="llama-server returned an unexpected payload")
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        return Completion(error="llama-server returned no choices")
    first = choices[0]
    if not isinstance(first, dict):
        return Completion(error="llama-server returned an unexpected choice")
    raw_message = first.get("message")
    message: dict[str, object] = raw_message if isinstance(raw_message, dict) else {}
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    usage = parsed.get("usage")
    counts = usage if isinstance(usage, dict) else {}
    return Completion(
        text=str(content or ""),
        finish_reason=str(first.get("finish_reason") or ""),
        prompt_tokens=_as_int(counts.get("prompt_tokens")),
        completion_tokens=_as_int(counts.get("completion_tokens")),
        reasoning=str(reasoning or ""),
    )


@dataclass(frozen=True)
class TrialResult:
    ok: bool
    stage: str
    command: tuple[str, ...]
    reply: str = ""
    error: str = ""
    log: tuple[str, ...] = ()


def trial_start(
    server: LlamaServer,
    command: Sequence[str],
    health_timeout: float = HEALTH_WAIT_SECONDS,
    generation_timeout: float = GENERATION_TIMEOUT_SECONDS,
    cwd: Path | None = None,
) -> TrialResult:
    argv = tuple(str(item) for item in command)
    status = server.start(command, cwd=cwd)
    if status.error:
        return TrialResult(False, STAGE_START, argv, error=status.error, log=tuple(server.log()))
    if not server.wait_healthy(health_timeout):
        log = tuple(server.log())
        exit_code = server.status().exit_code
        server.stop()
        detail = f"exit code {exit_code}" if exit_code is not None else "health check timed out"
        return TrialResult(False, STAGE_HEALTH, argv, error=detail, log=log)
    completion = chat_completion(server.base_url, TEST_MESSAGES, timeout=generation_timeout)
    if completion.error or not completion.text.strip():
        log = tuple(server.log())
        server.stop()
        return TrialResult(
            False,
            STAGE_GENERATION,
            argv,
            error=completion.error or "the model produced no text",
            log=log,
        )
    return TrialResult(True, STAGE_OK, argv, reply=completion.text.strip(), log=tuple(server.log()))


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    trial: TrialResult
    active: str
    rolled_back_to: str
    rollback_ok: bool


def apply_profile(
    connection: sqlite3.Connection,
    server: LlamaServer,
    binary: Path,
    profile: Profile,
    health_timeout: float = HEALTH_WAIT_SECONDS,
    generation_timeout: float = GENERATION_TIMEOUT_SECONDS,
) -> ApplyResult:
    previous = last_good_profile(connection)
    trial = trial_start(
        server,
        effective_command(binary, profile),
        health_timeout=health_timeout,
        generation_timeout=generation_timeout,
    )
    if trial.ok:
        mark_last_good(connection, profile.name)
        set_default_profile(connection, profile.name)
        return ApplyResult(True, trial, profile.name, "", True)
    server.stop()
    if previous is None or previous.name == profile.name:
        return ApplyResult(False, trial, "", "", False)
    server.start(effective_command(binary, previous))
    restored = server.wait_healthy(health_timeout)
    if not restored:
        server.stop()
    set_default_profile(connection, previous.name)
    return ApplyResult(False, trial, previous.name if restored else "", previous.name, restored)
