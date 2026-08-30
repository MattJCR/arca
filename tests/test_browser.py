from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn

from arca.api import create_app
from arca.config import ArcaConfig, init_library, library_db_path
from arca.db import open_library_rw
from arca.embed import EmbeddingClient
from arca.i18n import load_catalog
from arca.pipeline import ingest_documents
from tests.support import EmbeddingServer, article, build_epub, build_zim, free_port

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Browser, Page, sync_playwright

CHANNEL = "chrome"
HEADED = os.environ.get("ARCA_HEADED", "") not in ("", "0")
SLOW_MO = int(os.environ.get("ARCA_SLOW_MO", "250" if HEADED else "0"))
READY_SECONDS = 20.0
ARTICLE_TEXT = "Photosynthesis turns light into chemical energy inside the chloroplast."
PIECES = ("Una ", "respuesta ", "que ", "llega ", "en ", "trozos.")
PIECE_PAUSE = 0.3


class Server:
    def __init__(self, root: Path, chat_url: str = "", config: ArcaConfig | None = None) -> None:
        self.port = free_port()
        settings = config or (
            ArcaConfig(auto_engine=False, chat_url=chat_url)
            if chat_url
            else ArcaConfig(auto_engine=False)
        )
        served = uvicorn.Config(
            create_app(root, settings),
            host="127.0.0.1",
            port=self.port,
            log_level="error",
        )
        self._server = uvicorn.Server(served)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + READY_SECONDS
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.1)
        raise RuntimeError("the server never started")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=READY_SECONDS)


class ModelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for piece in PIECES:
                self._frame({"choices": [{"delta": {"content": piece}}]})
                time.sleep(PIECE_PAUSE)
            self._frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            return

    def _frame(self, payload: dict[str, object]) -> None:
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


class FakeModel:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", free_port()), ModelHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=READY_SECONDS)


@pytest.fixture(scope="module")
def model() -> Iterator[FakeModel]:
    instance = FakeModel()
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture(scope="module")
def talking(library: Path, model: FakeModel) -> Iterator[Server]:
    instance = Server(library, chat_url=model.url)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("browser") / "library"
    init_library(root)
    build_zim(
        root / "zim" / "manual.zim",
        [
            article("plants.html", "Photosynthesis", f"<p>{ARTICLE_TEXT}</p>"),
            article("pump.html", "Pump", "<p>The pump is primed through the plug.</p>"),
        ],
        title="Test manual",
        language="eng",
    )
    return root


@pytest.fixture(scope="module")
def server(library: Path) -> Iterator[Server]:
    instance = Server(library)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture(scope="module")
def driver() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                channel=CHANNEL, headless=not HEADED, slow_mo=SLOW_MO
            )
        except Exception as error:
            pytest.skip(f"no system chrome to drive: {error}")
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def page(driver: Browser, server: Server) -> Iterator[Page]:
    context = driver.new_context()
    opened = context.new_page()
    yield opened
    context.close()


@pytest.fixture
def fresh_page(driver: Browser) -> Iterator[Page]:
    context = driver.new_context()
    opened = context.new_page()
    yield opened
    context.close()


def _open_article(page: Page, server: Server) -> None:
    page.goto(server.url, wait_until="networkidle")
    page.fill("#search-q", "photosynthesis")
    page.click("#search-form button[type=submit]")
    page.wait_for_selector("#search-results .result-title")
    page.click("#search-results .result-title")
    page.wait_for_selector("#viewer-pane:not([hidden])")
    page.wait_for_function("document.getElementById('viewer-loading').hidden === true")


def test_the_viewer_opens_a_zim_article_inside_the_frame(page: Page, server: Server) -> None:
    _open_article(page, server)
    frame = page.frame_locator("#viewer-frame")
    assert ARTICLE_TEXT in frame.locator("body").inner_text()


def test_the_reader_we_inject_runs_inside_a_zim_article(page: Page, server: Server) -> None:
    _open_article(page, server)
    frame = page.frames[1]
    assert frame.evaluate("typeof window.parent.postMessage") == "function"
    injected = frame.evaluate("document.querySelectorAll('script[src$=\"selection.js\"]').length")
    assert injected == 1


def test_selecting_text_in_a_zim_article_reaches_the_assistant(page: Page, server: Server) -> None:
    _open_article(page, server)
    frame = page.frames[1]
    frame.evaluate(
        """() => {
            const node = document.querySelector('p');
            const range = document.createRange();
            range.selectNodeContents(node);
            const selection = document.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.wait_for_selector("#chat-selection:not([hidden])")
    captured = page.evaluate("JSON.stringify(chatState.selection)")
    detail = json.loads(captured)
    assert detail["source_kind"] == "zim"
    assert detail["source_id"]
    assert detail["path"] == "plants.html"
    assert "photosynthesis" in detail["text"].lower()


def test_the_question_carries_the_selection_and_the_open_page(page: Page, server: Server) -> None:
    _open_article(page, server)
    frame = page.frames[1]
    frame.evaluate(
        """() => {
            const range = document.createRange();
            range.selectNodeContents(document.querySelector('p'));
            const selection = document.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }"""
    )
    page.wait_for_selector("#chat-selection:not([hidden])")
    sent = page.evaluate("JSON.stringify(screenState())")
    payload = json.loads(sent)
    assert payload["selection"]["source_kind"] == "zim"
    assert payload["open_document"]["source_kind"] == "zim"
    assert payload["open_document"]["path"] == "plants.html"


def test_asking_the_assistant_reaches_the_api_and_renders_an_answer(
    page: Page, server: Server
) -> None:
    sent: list[str] = []
    page.on("request", lambda request: sent.append(f"{request.method} {request.url}"))
    page.goto(server.url, wait_until="networkidle")
    page.fill("#chat-input", "can you explain this?")
    page.click("#chat-form button[type=submit]")
    page.wait_for_selector(".chat-assistant")
    assert f"POST {server.url}/api/chat" in sent
    answer = page.inner_text(".chat-assistant")
    assert answer.strip()


def _open_talk(page: Page, server: Server) -> None:
    page.goto(f"{server.url}/talk", wait_until="networkidle")
    page.click("#talk-new")
    page.wait_for_selector("#talk-form:not([hidden])")


def test_the_talk_screen_answers_through_the_streaming_endpoint(page: Page, server: Server) -> None:
    sent: list[str] = []
    page.on("request", lambda request: sent.append(f"{request.method} {request.url}"))
    _open_talk(page, server)
    page.fill("#talk-input", "hola, que tal?")
    page.click("#talk-form button[type=submit]")
    page.wait_for_selector(".chat-assistant")
    page.wait_for_function("!talkState.busy")
    assert any(url.endswith("/message/stream") for url in sent)
    assert page.is_hidden("#talk-stop")
    assert "hola, que tal?" in page.inner_text(".chat-you .chat-text")
    assert page.inner_text(".chat-assistant .chat-text").strip()
    assert page.is_visible("#talk-form")
    page.wait_for_selector("#talk-threads .thread[aria-current=true]")


def test_the_talk_screen_follows_the_theme_the_reader_chose(page: Page, server: Server) -> None:
    page.goto(f"{server.url}/talk", wait_until="networkidle")
    page.click("[data-theme-choice=dark]")
    assert page.get_attribute("html", "data-theme") == "dark"
    page.click("[data-theme-choice=light]")
    assert page.get_attribute("html", "data-theme") == "light"


def _ask(page: Page, server: Server, question: str) -> None:
    page.goto(f"{server.url}/talk", wait_until="networkidle")
    page.click("#talk-new")
    page.wait_for_selector("#talk-form:not([hidden])")
    page.fill("#talk-input", question)
    page.click("#talk-form button[type=submit]")
    page.wait_for_function(
        "() => { const node = document.querySelector('.chat-assistant .chat-text');"
        " return node !== null && node.textContent.startsWith('Una '); }"
    )


def test_the_answer_shows_up_while_the_model_is_still_writing_it(
    page: Page, talking: Server
) -> None:
    _ask(page, talking, "cuentame algo")
    partial = page.inner_text(".chat-assistant .chat-text")
    assert page.is_visible("#talk-stop")
    assert len(partial) < len("".join(PIECES))
    page.wait_for_function("!talkState.busy")
    assert page.inner_text(".chat-assistant .chat-text") == "".join(PIECES)
    assert page.is_hidden("#talk-stop")


def test_stopping_the_answer_keeps_the_words_already_written(page: Page, talking: Server) -> None:
    _ask(page, talking, "corta esto a la mitad")
    page.click("#talk-stop")
    page.wait_for_function("!talkState.busy")
    kept = page.inner_text(".chat-assistant .chat-text")
    assert kept.startswith("Una")
    assert len(kept) < len("".join(PIECES))


BOOK_DIMS = 8
BOOK_TITLE = "Field handbook"
BOOK_FILLER = "words that the layout engine spreads across the pages of the book "
BOOK_TERM = "turbine"


def _book_paragraphs() -> list[str]:
    return [
        f"Paragraph {index} about the {BOOK_TERM}. " + BOOK_FILLER * 8 for index in range(1, 13)
    ]


@pytest.fixture(scope="module")
def embeddings() -> Iterator[EmbeddingServer]:
    instance = EmbeddingServer(dims=BOOK_DIMS)
    instance.start()
    yield instance
    instance.stop()


@pytest.fixture(scope="module")
def shelf(tmp_path_factory: pytest.TempPathFactory, embeddings: EmbeddingServer) -> Path:
    base = tmp_path_factory.mktemp("shelf")
    root = base / "library"
    init_library(root)
    source = build_epub(base / "inbox" / "handbook.epub", _book_paragraphs(), title=BOOK_TITLE)
    connection = open_library_rw(library_db_path(root))
    client = EmbeddingClient(base_url=embeddings.url, dims=BOOK_DIMS, timeout=5.0)
    try:
        report = ingest_documents(root, connection, [source], client)
    finally:
        connection.close()
    assert report.sealed is True
    return root


@pytest.fixture(scope="module")
def shelf_server(shelf: Path, embeddings: EmbeddingServer) -> Iterator[Server]:
    instance = Server(
        shelf,
        config=ArcaConfig(auto_engine=False, embed_url=embeddings.url, embed_dims=BOOK_DIMS),
    )
    instance.start()
    yield instance
    instance.stop()


def test_the_language_choice_survives_the_three_screens(fresh_page: Page, server: Server) -> None:
    english = load_catalog("en")
    fresh_page.goto(server.url, wait_until="networkidle")
    assert fresh_page.get_attribute("html", "lang") == "es"
    fresh_page.select_option("#lang-select", "en")
    fresh_page.wait_for_function("document.documentElement.lang === 'en'")
    screens = (
        ("/", "home.nav.archive"),
        ("/talk", "home.nav.talk"),
        ("/admin", "admin.nav.family.content"),
    )
    for path, key in screens:
        fresh_page.goto(f"{server.url}{path}", wait_until="networkidle")
        assert fresh_page.get_attribute("html", "lang") == "en", path
        assert english.text(key) in fresh_page.inner_text("body"), path


def test_a_typed_address_keeps_the_language_already_chosen(
    fresh_page: Page, server: Server
) -> None:
    fresh_page.goto(f"{server.url}/?lang=en", wait_until="networkidle")
    fresh_page.goto(f"{server.url}/talk", wait_until="networkidle")
    fresh_page.reload(wait_until="networkidle")
    assert fresh_page.get_attribute("html", "lang") == "en"


def test_a_book_is_searched_read_and_downloaded(fresh_page: Page, shelf_server: Server) -> None:
    fresh_page.goto(shelf_server.url, wait_until="networkidle")
    fresh_page.fill("#search-q", BOOK_TERM)
    fresh_page.click("#search-form button[type=submit]")
    fresh_page.wait_for_selector("#search-results .result-title")
    assert BOOK_TITLE in fresh_page.inner_text("#search-results")
    assert load_catalog("es").text("home.result.download_original") in fresh_page.inner_text(
        "#search-results"
    )
    fresh_page.click("#search-results .result-actions a:first-child")
    fresh_page.wait_for_selector("#viewer-pane:not([hidden])")
    fresh_page.wait_for_function("document.getElementById('viewer-loading').hidden === true")
    frame = fresh_page.frame_locator("#viewer-frame")
    assert BOOK_TERM in frame.locator("body").inner_text().lower()
    assert frame.locator("[id^='p:0001']").first.is_visible()


def test_the_original_of_a_book_comes_down_as_a_file(
    fresh_page: Page, shelf_server: Server
) -> None:
    fresh_page.goto(shelf_server.url, wait_until="networkidle")
    fresh_page.fill("#search-q", BOOK_TERM)
    fresh_page.click("#search-form button[type=submit]")
    fresh_page.wait_for_selector("#search-results .result-actions")
    with fresh_page.expect_download() as caught:
        fresh_page.click("#search-results .result-actions a:last-child")
    download = caught.value
    assert download.suggested_filename.endswith(".epub")
