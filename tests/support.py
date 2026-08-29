from __future__ import annotations

import hashlib
import io
import json
import re
import socket
import struct
import sys
import tarfile
import threading
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from libzim.writer import Creator, Hint, Item, StringProvider

from arca.ingest import (
    Extraction,
    OriginalRecord,
    anchor,
    render_readable,
)
from arca.zim import ZimInfo, archive_info, open_archive

LLAMA_STUB = Path(__file__).resolve().parent / "fixtures" / "llama_server_stub.py"


def stub_binary() -> str:
    return sys.executable


def stub_args(port: int, *extra: str) -> list[str]:
    return [str(LLAMA_STUB), "--host", "127.0.0.1", "--port", str(port), *extra]


def stub_command(port: int, *extra: str) -> list[str]:
    return [stub_binary(), *stub_args(port, *extra)]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def write_gguf(
    path: Path,
    architecture: str = "llama",
    context: int = 8192,
    embedding: int = 4096,
    blocks: int = 32,
    padding: int = 32,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray(b"GGUF")
    payload += struct.pack("<I", 3)
    payload += struct.pack("<Q", 291)
    entries: list[tuple[str, int, str | int]] = [
        ("general.architecture", 8, architecture),
        ("general.name", 8, "Test model"),
        ("general.file_type", 4, 15),
        (f"{architecture}.context_length", 4, context),
        (f"{architecture}.embedding_length", 4, embedding),
        (f"{architecture}.block_count", 4, blocks),
    ]
    payload += struct.pack("<Q", len(entries))
    for key, value_type, value in entries:
        encoded = key.encode("utf-8")
        payload += struct.pack("<Q", len(encoded)) + encoded
        payload += struct.pack("<I", value_type)
        if value_type == 8:
            text = str(value).encode("utf-8")
            payload += struct.pack("<Q", len(text)) + text
        else:
            payload += struct.pack("<I", int(value))
    payload += b"\x00" * padding
    path.write_bytes(bytes(payload))
    return path


@dataclass(frozen=True)
class ZimArticle:
    path: str
    title: str
    body: str
    mimetype: str = "text/html"


class _BuilderItem(Item):
    def __init__(self, article: ZimArticle) -> None:
        super().__init__()
        self._article = article

    def get_path(self) -> str:
        return self._article.path

    def get_title(self) -> str:
        return self._article.title

    def get_mimetype(self) -> str:
        return self._article.mimetype

    def get_contentprovider(self) -> StringProvider:
        return StringProvider(self._article.body)

    def get_hints(self) -> dict[Hint, bool]:
        return {Hint.FRONT_ARTICLE: self._article.mimetype == "text/html"}


def build_zim(
    path: Path,
    articles: Iterable[ZimArticle],
    *,
    title: str = "Test",
    description: str = "Test archive",
    language: str = "eng",
    main_path: str = "",
    fulltext: bool = True,
    metadata: Mapping[str, str] | None = None,
    redirects: Iterable[tuple[str, str, str]] = (),
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = list(articles)
    with Creator(path).config_indexing(fulltext, language) as creator:
        creator.add_metadata("Title", title)
        creator.add_metadata("Description", description)
        creator.add_metadata("Language", language)
        for key, value in (metadata or {}).items():
            creator.add_metadata(key, value)
        for article in entries:
            creator.add_item(_BuilderItem(article))
        for source, redirect_title, target in redirects:
            creator.add_redirection(source, redirect_title, target, {Hint.FRONT_ARTICLE: False})
        target = main_path or (entries[0].path if entries else "")
        if target:
            creator.set_mainpath(target)
    return path


def article(path: str, title: str, body: str) -> ZimArticle:
    return ZimArticle(
        path, title, f"<html><head><title>{title}</title></head><body>{body}</body></html>"
    )


@dataclass
class Behavior:
    payload: bytes = b""
    mode: str = "range"
    fail_after: int = 0
    requests: list[str | None] = field(default_factory=list)
    bytes_served: int = 0


class DownloadServer:
    def __init__(self) -> None:
        self.behaviors: dict[str, Behavior] = {}
        behaviors = self.behaviors

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?")[0]
                behavior = behaviors.get(path)
                if behavior is None:
                    self.send_error(404)
                    return
                range_header = self.headers.get("Range")
                behavior.requests.append(range_header)
                start = 0
                if behavior.mode == "range" and range_header is not None:
                    start = int(range_header.removeprefix("bytes=").rstrip("-"))
                    if start >= len(behavior.payload):
                        self.send_error(416)
                        return
                    self.send_response(206)
                    self.send_header(
                        "Content-Range",
                        f"bytes {start}-{len(behavior.payload) - 1}/{len(behavior.payload)}",
                    )
                else:
                    self.send_response(200)
                body = behavior.payload[start:]
                if behavior.mode == "fail_after":
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    served = body[: behavior.fail_after]
                    self.wfile.write(served)
                    behavior.bytes_served += len(served)
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                behavior.bytes_served += len(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def url(self, path: str) -> str:
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}{path}"

    def add(self, path: str, behavior: Behavior) -> Behavior:
        self.behaviors[path] = behavior
        return behavior


@dataclass(frozen=True)
class TreeEntry:
    size: int
    mtime_ns: int


def snapshot_tree(root: Path) -> dict[str, TreeEntry]:
    snapshot: dict[str, TreeEntry] = {}
    if not root.is_dir():
        return snapshot
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root)).replace("\\", "/")
        if path.is_dir():
            snapshot[relative + "/"] = TreeEntry(-1, -1)
            continue
        stat = path.stat()
        snapshot[relative] = TreeEntry(stat.st_size, stat.st_mtime_ns)
    return snapshot


def tree_difference(
    before: dict[str, TreeEntry], after: dict[str, TreeEntry]
) -> tuple[list[str], list[str], list[str]]:
    created = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    return created, removed, changed


def build_pdf(
    path: Path,
    pages: Iterable[Iterable[str]],
    *,
    title: str = "",
    author: str = "",
    fontsize: int = 11,
) -> Path:
    import pymupdf

    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    for blocks in pages:
        page = document.new_page()
        top = 90
        for text in blocks:
            page.insert_textbox(
                pymupdf.Rect(60, top, 540, top + 120), text, fontsize=fontsize, align=0
            )
            top += 140
    metadata = {"title": title, "author": author}
    document.set_metadata({key: value for key, value in metadata.items() if value})
    document.save(path)
    document.close()
    return path


def blank_pdf(path: Path, pages: int = 1) -> Path:
    import pymupdf

    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page()
    document.save(path)
    document.close()
    return path


def bag_of_words_vector(text: str, dims: int) -> list[float]:
    vector = [0.0] * dims
    body = text.split(": ", 1)[-1].lower()
    for word in re.findall(r"[a-z0-9]+", body):
        if len(word) < 4:
            continue
        bucket = int.from_bytes(hashlib.md5(word.encode()).digest()[:4], "big") % dims
        vector[bucket] += 1.0
    if not any(vector):
        vector[0] = 1.0
    norm = sum(value * value for value in vector) ** 0.5
    return [value / norm for value in vector]


class EmbeddingServer:
    def __init__(self, dims: int = 8, mode: str = "length") -> None:
        self.dims = dims
        self.mode = mode
        self.requests: list[dict[str, object]] = []
        self.status = 200
        self.healthy = True
        self.drop_last = False
        self.fail_after = 0
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/health" and state.healthy:
                    self._reply(200, {"status": "ok"})
                    return
                self.send_error(503)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                state.requests.append(payload)
                spent = state.fail_after and len(state.requests) > state.fail_after
                if state.status != 200 or spent:
                    self.send_error(state.status if state.status != 200 else 500)
                    return
                inputs = payload.get("input") or []
                data = [
                    {"index": index, "embedding": state.vector(text, index)}
                    for index, text in enumerate(inputs)
                ]
                if state.drop_last:
                    data = data[:-1]
                self._reply(200, {"data": data, "model": payload.get("model", "")})

            def _reply(self, code: int, body: dict[str, object]) -> None:
                encoded = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def vector(self, text: str, index: int) -> list[float]:
        if self.mode == "hashed":
            return bag_of_words_vector(text, self.dims)
        return [float(len(text) + index)] * self.dims

    def texts(self) -> list[str]:
        collected: list[str] = []
        for payload in self.requests:
            value = payload.get("input")
            if isinstance(value, list):
                collected.extend(str(item) for item in value)
        return collected


class ModelHubServer:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, object]] = {}
        self.files: dict[str, list[dict[str, object]]] = {}
        self.gated: set[str] = set()
        self.requests: list[str] = []
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                state.requests.append(self.path)
                path = self.path.split("?")[0]
                if path == "/api/models":
                    self._reply(200, list(state.models.values()))
                    return
                if path.endswith("/tree/main"):
                    model_id = path.removeprefix("/api/models/").removesuffix("/tree/main")
                    if model_id in state.gated:
                        self.send_error(401)
                        return
                    self._reply(200, state.files.get(model_id, []))
                    return
                if path.startswith("/api/models/"):
                    model_id = path.removeprefix("/api/models/")
                    if model_id in state.gated:
                        self.send_error(401)
                        return
                    if model_id not in state.models:
                        self.send_error(404)
                        return
                    self._reply(200, state.models[model_id])
                    return
                self.send_error(404)

            def _reply(self, code: int, body: object) -> None:
                encoded = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def add(
        self,
        model_id: str,
        files: list[tuple[str, int, str]],
        downloads: int = 100,
        gated: bool = False,
    ) -> None:
        self.models[model_id] = {
            "id": model_id,
            "downloads": downloads,
            "likes": 7,
            "gated": gated,
            "lastModified": "2026-08-01T10:00:00.000Z",
            "tags": ["gguf", "text-generation"],
        }
        self.files[model_id] = [
            {"type": "file", "path": name, "size": size, "lfs": {"oid": oid, "size": size}}
            if oid
            else {"type": "file", "path": name, "size": size}
            for name, size, oid in files
        ]
        if gated:
            self.gated.add(model_id)


class ReleaseServer:
    def __init__(self, repo: str = "ggml-org/llama.cpp") -> None:
        self.repo = repo
        self.tag = "b9999"
        self.commit = "a" * 40
        self.commitish = "a" * 40
        self.decoy_tag = "v0.3.0"
        self.assets: dict[str, bytes] = {}
        self.decoy_assets: dict[str, bytes] = {"nightly-tag.txt": b"b9999"}
        self.requests: list[str] = []
        self.tag_reachable = True
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                state.requests.append(self.path)
                path = self.path.split("?")[0]
                if path == f"/repos/{state.repo}/releases":
                    self._json(200, state.release_list())
                    return
                if path == f"/repos/{state.repo}/git/ref/tags/{state.tag}":
                    if not state.tag_reachable:
                        self.send_error(404)
                        return
                    self._json(200, {"object": {"sha": state.commit}})
                    return
                name = path.removeprefix("/assets/")
                payload = state.assets.get(name) or state.decoy_assets.get(name)
                if payload is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _json(self, code: int, body: object) -> None:
                encoded = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def add(self, name: str, payload: bytes) -> None:
        self.assets[name] = payload

    def release_list(self) -> list[dict[str, object]]:
        return [
            self.release(self.decoy_tag, "master", self.decoy_assets),
            self.release(self.tag, self.commitish, self.assets),
        ]

    def release(self, tag: str, commitish: str, assets: dict[str, bytes]) -> dict[str, object]:
        return {
            "tag_name": tag,
            "target_commitish": commitish,
            "prerelease": True,
            "assets": [
                {
                    "name": name,
                    "browser_download_url": f"{self.url}/assets/{name}",
                    "size": len(payload),
                    "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                }
                for name, payload in assets.items()
            ],
        }


def engine_package(binary_name: str = "llama-server.exe", extra: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(f"build/bin/{binary_name}", "binary\n")
        bundle.writestr("build/bin/ggml.dll", "library\n")
        if extra:
            bundle.writestr(extra, "extra\n")
    return buffer.getvalue()


def engine_tarball(binary_name: str = "llama-server") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        entries = ((f"build/bin/{binary_name}", b"binary\n"), ("build/bin/libggml.so", b"lib\n"))
        for name, body in entries:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            bundle.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def runtime_package(name: str = "cudart64_12.dll") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(name, "runtime\n")
    return buffer.getvalue()


def read_info(path: Path) -> ZimInfo:
    return archive_info(open_archive(path), path)


def readable_html(record: OriginalRecord, extraction: Extraction) -> str:
    blocks = [
        (block.page, block.ord, anchor(block.page, block.ord), block.text)
        for block in extraction.blocks
    ]
    return render_readable(
        record.title, record.orig_name, record.doc_id, blocks, extraction.unindexable_pages
    )
