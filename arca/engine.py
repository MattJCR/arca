from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

from arca.config import ArcaConfig, library_db_path
from arca.db import open_library_ro, open_library_rw, record_event, utc_now_iso
from arca.fetch import QueueWorker, enqueue, file_sha256, get_download
from arca.llama import (
    BINARY_NAMES,
    MODE_FORM,
    LlamaServer,
    ModelFile,
    Profile,
    apply_profile,
    chat_model_file,
    find_binary,
    inspect_binary,
    last_good_profile,
    save_profile,
)
from arca.vendor import (
    VendorError,
    capture_llama_help,
    default_lock_path,
    enqueue_vendor_downloads,
    is_commit,
    llama_lock_entry,
    write_lock_entry,
)

GITHUB_API_URL = "https://api.github.com"
LLAMA_REPO = "ggml-org/llama.cpp"
USER_AGENT = "arca"
TIMEOUT_SECONDS = 20.0
VENDOR_SUBDIR = ("vendor", "llama.cpp")
DOWNLOAD_KIND = "engine"
PROFILE_NAME = "auto"
CONTEXT_SIZE = "8192"
CACHE_TYPE = "q8_0"
ALL_GPU_LAYERS = "999"
DEFAULT_CHAT_HOST = "127.0.0.1"
DEFAULT_CHAT_PORT = "8081"
POLL_SECONDS = 0.5
DOWNLOAD_TIMEOUT_SECONDS = 7200.0
ARCHIVE_SUFFIX = ".zip"
DIGEST_PREFIX = "sha256:"
SHA256_LENGTH = 64

BACKEND_CPU = "cpu"
BACKEND_ORDER = ("cuda", "hip", "rocm", "sycl", "vulkan", "metal", BACKEND_CPU)
GPU_BACKENDS = ("cuda", "hip", "rocm", "sycl", "vulkan", "metal")
SYSTEM_TOKENS = {
    "windows": ("win",),
    "linux": ("ubuntu", "linux"),
    "darwin": ("macos",),
}
MACHINE_TOKENS = {
    "amd64": ("x64", "x86_64"),
    "x86_64": ("x64", "x86_64"),
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
}

STATE_IDLE = "idle"
STATE_WAITING_MODEL = "waiting_model"
STATE_RESOLVING = "resolving"
STATE_DOWNLOADING = "downloading"
STATE_DEPLOYING = "deploying"
STATE_TESTING = "testing"
STATE_READY = "ready"
STATE_NO_NETWORK = "no_network"
STATE_UNSUPPORTED = "unsupported"
STATE_EXHAUSTED = "exhausted"
STATE_FAILED = "failed"


class EngineError(RuntimeError):
    pass


class EngineUnavailableError(EngineError):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class Release:
    tag: str
    commit: str
    assets: tuple[Asset, ...]


@dataclass(frozen=True)
class EngineStatus:
    state: str
    detail: str = ""
    candidate: str = ""
    download_id: int = 0
    tried: tuple[str, ...] = ()
    binary: str = ""
    profile: str = ""
    tag: str = ""
    updated_at: str = ""


def vendor_dir(root: Path) -> Path:
    return root.joinpath(*VENDOR_SUBDIR)


def releases_url(base_url: str = GITHUB_API_URL, repo: str = LLAMA_REPO) -> str:
    return f"{base_url.rstrip('/')}/repos/{repo}/releases/latest"


def tag_url(tag: str, base_url: str = GITHUB_API_URL, repo: str = LLAMA_REPO) -> str:
    return f"{base_url.rstrip('/')}/repos/{repo}/git/ref/tags/{urllib.parse.quote(tag)}"


def resolve_release(base_url: str = GITHUB_API_URL, repo: str = LLAMA_REPO) -> Release:
    payload = _fetch(releases_url(base_url, repo))
    if not isinstance(payload, dict):
        raise EngineError("the release feed returned an unexpected payload")
    raw_assets = payload.get("assets")
    assets = (
        tuple(_asset(item) for item in raw_assets if isinstance(item, dict))
        if isinstance(raw_assets, list)
        else ()
    )
    return Release(
        tag=str(payload.get("tag_name") or ""),
        commit=_sha(payload.get("target_commitish")),
        assets=tuple(item for item in assets if item.url),
    )


def resolve_commit(tag: str, base_url: str = GITHUB_API_URL, repo: str = LLAMA_REPO) -> str:
    if not tag:
        return ""
    try:
        payload = _fetch(tag_url(tag, base_url, repo))
    except EngineError:
        return ""
    if not isinstance(payload, dict):
        return ""
    target = payload.get("object")
    return _sha(target.get("sha")) if isinstance(target, dict) else ""


def backend_of(name: str) -> str:
    lowered = name.lower()
    for backend in BACKEND_ORDER:
        if backend in lowered:
            return backend
    return BACKEND_CPU


def uses_gpu(name: str) -> bool:
    return backend_of(name) in GPU_BACKENDS


def matches_platform(name: str, system: str, machine: str) -> bool:
    lowered = name.lower()
    systems = SYSTEM_TOKENS.get(system, ())
    machines = MACHINE_TOKENS.get(machine, ())
    if not systems or not machines:
        return False
    if not any(token in lowered for token in systems):
        return False
    return any(token in lowered for token in machines)


def candidates(assets: tuple[Asset, ...], system: str = "", machine: str = "") -> tuple[Asset, ...]:
    resolved_system = (system or platform.system()).lower()
    resolved_machine = (machine or platform.machine()).lower()
    found = [
        asset
        for asset in assets
        if asset.name.lower().endswith(ARCHIVE_SUFFIX)
        and matches_platform(asset.name, resolved_system, resolved_machine)
    ]
    found.sort(key=lambda asset: (BACKEND_ORDER.index(backend_of(asset.name)), asset.name))
    return tuple(found)


def extract_archive(archive: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            targets = [_member_target(root, member.filename) for member in members]
            for member, target in zip(members, targets, strict=True):
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
    except (zipfile.BadZipFile, ValueError) as error:
        raise EngineError(f"the engine package is not a readable archive: {error}") from error
    _mark_executable(dest)
    return dest


def locate_binary(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    for name in BINARY_NAMES:
        for found in sorted(directory.rglob(name)):
            if found.is_file():
                return found
    return None


def chat_endpoint(chat_url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(chat_url)
    host = parts.hostname or DEFAULT_CHAT_HOST
    port = str(parts.port) if parts.port else DEFAULT_CHAT_PORT
    return host, port


def default_profile(model: ModelFile, chat_url: str, gpu: bool) -> Profile:
    host, port = chat_endpoint(chat_url)
    args = {
        "--model": str(model.path),
        "--ctx-size": CONTEXT_SIZE,
        "--cache-type-k": CACHE_TYPE,
        "--cache-type-v": CACHE_TYPE,
        "--host": host,
        "--port": port,
    }
    if gpu:
        args["--n-gpu-layers"] = ALL_GPU_LAYERS
    return Profile(name=PROFILE_NAME, mode=MODE_FORM, args=args)


class EngineBringup:
    def __init__(
        self,
        root: Path,
        config: ArcaConfig,
        server: LlamaServer,
        worker: QueueWorker,
        *,
        api_url: str = GITHUB_API_URL,
        repo: str = LLAMA_REPO,
        health_timeout: float = 0.0,
        generation_timeout: float = 0.0,
        download_timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
    ) -> None:
        self._root = root
        self._config = config
        self._server = server
        self._worker = worker
        self._api_url = api_url
        self._repo = repo
        self._health_timeout = health_timeout
        self._generation_timeout = generation_timeout
        self._download_timeout = download_timeout
        self._lock = threading.Lock()
        self._status = EngineStatus(STATE_IDLE, updated_at=utc_now_iso())
        self._thread: threading.Thread | None = None

    def status(self) -> EngineStatus:
        with self._lock:
            return self._status

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> EngineStatus:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._status
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()
            return self._status

    def run(self) -> EngineStatus:
        try:
            return self._bringup()
        except EngineUnavailableError as error:
            return self._set(STATE_NO_NETWORK, detail=str(error))
        except (EngineError, VendorError, OSError, sqlite3.Error) as error:
            return self._set(STATE_FAILED, detail=str(error))

    def _bringup(self) -> EngineStatus:
        model = chat_model_file(self._root, self._config.chat_model)
        if model is None:
            return self._set(STATE_WAITING_MODEL)
        present, _ = find_binary(self._root, self._config.llama_bin)
        if present is not None:
            settled = self._settled()
            if settled is not None:
                return self._set(STATE_READY, binary=str(present), profile=settled)
            gpu = inspect_binary(self._root, self._config.llama_bin).gpu
            return self._activate(present, model, gpu, tag="")
        self._set(STATE_RESOLVING)
        release = resolve_release(self._api_url, self._repo)
        picks = candidates(release.assets)
        if not picks:
            return self._set(STATE_UNSUPPORTED, tag=release.tag)
        tried: list[str] = []
        for asset in picks:
            tried.append(asset.name)
            outcome = self._try_candidate(asset, release, model, tuple(tried))
            if outcome is not None:
                return outcome
        return self._set(STATE_EXHAUSTED, tried=tuple(tried), tag=release.tag)

    def _try_candidate(
        self, asset: Asset, release: Release, model: ModelFile, tried: tuple[str, ...]
    ) -> EngineStatus | None:
        self._set(STATE_DOWNLOADING, candidate=asset.name, tried=tried, tag=release.tag)
        archive = self._obtain(asset)
        if archive is None:
            self._record("warning", f"engine package failed to download: {asset.name}")
            return None
        target = vendor_dir(self._root) / f"{release.tag}-{backend_of(asset.name)}"
        self._set(STATE_DEPLOYING, candidate=asset.name, tried=tried, tag=release.tag)
        try:
            extract_archive(archive, target)
        except EngineError as error:
            self._record(
                "warning", f"engine package could not be unpacked: {asset.name}", str(error)
            )
            _discard(target)
            return None
        binary = locate_binary(target)
        if binary is None:
            self._record("warning", f"engine package carries no server binary: {asset.name}")
            _discard(target)
            return None
        self._set(STATE_TESTING, candidate=asset.name, tried=tried, tag=release.tag)
        status = self._activate(binary, model, uses_gpu(asset.name), tag=release.tag, tried=tried)
        if status.state != STATE_READY:
            _discard(target)
            return None
        self._pin(release)
        return status

    def _activate(
        self,
        binary: Path,
        model: ModelFile,
        gpu: bool,
        *,
        tag: str,
        tried: tuple[str, ...] = (),
    ) -> EngineStatus:
        profile = default_profile(model, self._config.chat_url, gpu)
        connection = open_library_rw(library_db_path(self._root))
        try:
            stored = save_profile(connection, profile)
            outcome = apply_profile(
                connection,
                self._server,
                binary,
                stored,
                **self._timeouts(),
            )
            record_event(
                connection,
                "info" if outcome.ok else "warning",
                "engine",
                f"engine trial {binary.name}: {outcome.trial.stage}",
                outcome.trial.error,
            )
        finally:
            connection.close()
        if not outcome.ok:
            return self._set(
                STATE_FAILED,
                detail=outcome.trial.error,
                candidate=binary.name,
                tried=tried,
                tag=tag,
            )
        capture_llama_help(self._root, str(binary))
        return self._set(
            STATE_READY,
            binary=str(binary),
            profile=stored.name,
            tried=tried,
            tag=tag,
        )

    def _settled(self) -> str | None:
        connection = open_library_rw(library_db_path(self._root))
        try:
            profile = last_good_profile(connection)
        finally:
            connection.close()
        return profile.name if profile is not None else None

    def _timeouts(self) -> dict[str, float]:
        chosen: dict[str, float] = {}
        if self._health_timeout:
            chosen["health_timeout"] = self._health_timeout
        if self._generation_timeout:
            chosen["generation_timeout"] = self._generation_timeout
        return chosen

    def _obtain(self, asset: Asset) -> Path | None:
        dest = vendor_dir(self._root) / asset.name
        if dest.is_file() and (not asset.sha256 or file_sha256(dest) == asset.sha256):
            return dest
        connection = open_library_rw(library_db_path(self._root))
        try:
            download_id = enqueue(
                connection,
                kind=DOWNLOAD_KIND,
                ref=asset.name,
                filename=asset.name,
                dest_path=dest,
                size_bytes=asset.size_bytes or None,
                sha256=asset.sha256,
                mirrors=(asset.url,),
            )
            record_event(connection, "info", "engine", f"queued engine package: {asset.name}")
        finally:
            connection.close()
        with self._lock:
            self._status = replace(self._status, download_id=download_id, updated_at=utc_now_iso())
        self._worker.kick()
        return self._wait(download_id, dest)

    def _wait(self, download_id: int, dest: Path) -> Path | None:
        deadline = time.monotonic() + self._download_timeout
        while time.monotonic() < deadline:
            connection = open_library_ro(library_db_path(self._root))
            try:
                row = get_download(connection, download_id)
            finally:
                connection.close()
            if row is None:
                return None
            status = str(row["status"])
            if status == "done":
                return dest if dest.is_file() else None
            if status in ("failed", "cancelled"):
                return None
            time.sleep(POLL_SECONDS)
        return None

    def _pin(self, release: Release) -> None:
        commit = release.commit or resolve_commit(release.tag, self._api_url, self._repo)
        if not is_commit(commit):
            self._record("warning", f"llama.cpp release {release.tag} exposes no commit to pin")
            return
        entries = write_lock_entry(default_lock_path(), llama_lock_entry(commit))
        connection = open_library_rw(library_db_path(self._root))
        try:
            queued = enqueue_vendor_downloads(connection, self._root, entries)
            record_event(
                connection, "info", "engine", f"llama.cpp pinned at {commit} ({release.tag})"
            )
        finally:
            connection.close()
        if queued:
            self._worker.kick()

    def _record(self, level: str, message: str, detail: str = "") -> None:
        connection = open_library_rw(library_db_path(self._root))
        try:
            record_event(connection, level, "engine", message, detail)
        finally:
            connection.close()

    def _set(self, state: str, **fields: object) -> EngineStatus:
        status = EngineStatus(state, updated_at=utc_now_iso(), **fields)  # type: ignore[arg-type]
        with self._lock:
            self._status = status
        return status


def _discard(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def _member_target(root: Path, name: str) -> Path:
    cleaned = name.replace("\\", "/")
    parts = _member_parts(cleaned)
    if not parts:
        raise EngineError(f"unsafe archive member: {name}")
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root):
        raise EngineError(f"unsafe archive member: {name}")
    return target


def _member_parts(cleaned: str) -> list[str]:
    if cleaned.startswith("/") or ":" in cleaned:
        return []
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return []
    return parts


def _mark_executable(directory: Path) -> None:
    if os.name == "nt":
        return
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix in (".txt", ".md", ".h", ".hpp"):
            continue
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _asset(item: dict[str, object]) -> Asset:
    return Asset(
        name=str(item.get("name") or ""),
        url=str(item.get("browser_download_url") or ""),
        size_bytes=_as_int(item.get("size")),
        sha256=_digest(item.get("digest")),
    )


def _digest(value: object) -> str:
    text = str(value or "")
    if not text.startswith(DIGEST_PREFIX):
        return ""
    return _sha(text.removeprefix(DIGEST_PREFIX))


def _sha(value: object) -> str:
    text = str(value or "").strip().lower()
    if len(text) != SHA256_LENGTH and len(text) != 40:
        return ""
    return text if all(char in "0123456789abcdef" for char in text) else ""


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _fetch(url: str) -> object:
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise EngineError(f"unsupported url scheme: {url}")
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw: bytes = response.read()
    except urllib.error.HTTPError as error:
        raise EngineUnavailableError(f"the release feed returned HTTP {error.code}") from error
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        raise EngineUnavailableError(f"the release feed is unreachable: {error}") from error
    try:
        return json.loads(raw)
    except ValueError as error:
        raise EngineError(f"the release feed returned invalid json: {error}") from error
