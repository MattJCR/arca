from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace

from arca.llama import GgufError, header_of

DEFAULT_HUB_URL = "https://huggingface.co"
SEARCH_PATH = "/api/models"
TREE_PATH = "/tree/main"
RESOLVE_PATH = "/resolve/main/"
GGUF_FILTER = "gguf"
GGUF_SUFFIX = ".gguf"
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
TIMEOUT_SECONDS = 20.0
USER_AGENT = "arca"
HEADER_BYTES = 262144
PROBE_LIMIT = 12
QUANT_PATTERN = re.compile(
    r"(IQ\d+[A-Z0-9_]*|Q\d+(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4)(?:-\d{5}-of-\d{5})?$",
    re.IGNORECASE,
)
SHARD_PATTERN = re.compile(r"-(\d{5})-of-(\d{5})$")
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][\w.-]{0,95}/[A-Za-z0-9][\w.-]{0,95}$")


class HubError(RuntimeError):
    pass


class HubUnavailableError(HubError):
    pass


class HubGatedError(HubError):
    pass


@dataclass(frozen=True)
class HubModel:
    model_id: str
    author: str
    name: str
    downloads: int
    likes: int
    gated: bool
    updated_at: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class HubFile:
    path: str
    size_bytes: int
    sha256: str
    quantization: str
    part: str
    architecture: str = ""
    dims: int = 0


def is_model_id(value: str) -> bool:
    return bool(MODEL_ID_PATTERN.match(value))


def quantization_of(filename: str) -> str:
    stem = filename.removesuffix(GGUF_SUFFIX)
    found = QUANT_PATTERN.search(stem)
    if found is None:
        return ""
    return found.group(1).upper()


def part_of(filename: str) -> str:
    found = SHARD_PATTERN.search(filename.removesuffix(GGUF_SUFFIX))
    return f"{found.group(1)}/{found.group(2)}" if found else ""


def search_url(base_url: str, query: str, limit: int = DEFAULT_LIMIT) -> str:
    params = {
        "search": query,
        "filter": GGUF_FILTER,
        "sort": "downloads",
        "direction": "-1",
        "limit": str(max(1, min(limit, MAX_LIMIT))),
    }
    return f"{base_url.rstrip('/')}{SEARCH_PATH}?{urllib.parse.urlencode(params)}"


def model_url(base_url: str, model_id: str) -> str:
    return f"{base_url.rstrip('/')}{SEARCH_PATH}/{model_id}"


def tree_url(base_url: str, model_id: str) -> str:
    return f"{model_url(base_url, model_id)}{TREE_PATH}?recursive=1"


def download_url(base_url: str, model_id: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{model_id}{RESOLVE_PATH}{urllib.parse.quote(path)}"


def search_models(
    base_url: str, query: str, limit: int = DEFAULT_LIMIT, token: str = ""
) -> list[HubModel]:
    payload = _fetch(search_url(base_url, query, limit), token)
    if not isinstance(payload, list):
        raise HubError("the model hub returned an unexpected payload")
    return [_model(item) for item in payload if isinstance(item, dict)]


def model_files(base_url: str, model_id: str, token: str = "") -> tuple[HubModel, list[HubFile]]:
    if not is_model_id(model_id):
        raise HubError(f"not a model identifier: {model_id}")
    detail = _fetch(model_url(base_url, model_id), token)
    if not isinstance(detail, dict):
        raise HubError("the model hub returned an unexpected payload")
    model = _model(detail)
    tree = _fetch(tree_url(base_url, model_id), token)
    if not isinstance(tree, list):
        raise HubError("the model hub returned an unexpected file list")
    files = [_file(item) for item in tree if _is_gguf(item)]
    files.sort(key=lambda item: (item.quantization, item.path))
    return model, [_inspected(base_url, model_id, item, token) for item in files[:PROBE_LIMIT]]


def _inspected(base_url: str, model_id: str, item: HubFile, token: str) -> HubFile:
    if item.part:
        return item
    architecture, dims = probe_gguf(download_url(base_url, model_id, item.path), token)
    return replace(item, architecture=architecture, dims=dims)


def _is_gguf(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    path = item.get("path")
    return isinstance(path, str) and path.lower().endswith(GGUF_SUFFIX)


def _model(item: dict[str, object]) -> HubModel:
    model_id = str(item.get("id") or item.get("modelId") or "")
    author, _, name = model_id.partition("/")
    tags = item.get("tags")
    return HubModel(
        model_id=model_id,
        author=author,
        name=name or model_id,
        downloads=_as_int(item.get("downloads")),
        likes=_as_int(item.get("likes")),
        gated=bool(item.get("gated")),
        updated_at=str(item.get("lastModified") or ""),
        tags=tuple(tag for tag in tags if isinstance(tag, str)) if isinstance(tags, list) else (),
    )


def _file(item: object) -> HubFile:
    entry = item if isinstance(item, dict) else {}
    path = str(entry.get("path") or "")
    lfs = entry.get("lfs")
    oid = ""
    size = _as_int(entry.get("size"))
    if isinstance(lfs, dict):
        oid = str(lfs.get("oid") or "")
        size = _as_int(lfs.get("size")) or size
    return HubFile(
        path=path,
        size_bytes=size,
        sha256=oid if len(oid) == 64 else "",
        quantization=quantization_of(path),
        part=part_of(path),
    )


def probe_gguf(url: str, token: str = "", window: int = HEADER_BYTES) -> tuple[str, int]:
    headers = {"User-Agent": USER_AGENT, "Range": f"bytes=0-{window - 1}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw: bytes = response.read(window)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return "", 0
    try:
        header = header_of(raw)
    except GgufError:
        return "", 0
    architecture = str(header.get("general.architecture") or "")
    dims = header.get(f"{architecture}.embedding_length") if architecture else None
    return architecture, int(dims) if isinstance(dims, int) else 0


def _fetch(url: str, token: str) -> object:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw: bytes = response.read()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise HubGatedError(f"the model hub refused access: HTTP {error.code}") from error
        raise HubUnavailableError(f"the model hub returned HTTP {error.code}") from error
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as error:
        raise HubUnavailableError(f"the model hub is unreachable: {error}") from error
    try:
        return json.loads(raw)
    except ValueError as error:
        raise HubError(f"the model hub returned invalid json: {error}") from error


def _as_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0
