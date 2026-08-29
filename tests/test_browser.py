from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from arca.api import create_app
from arca.config import ArcaConfig, init_library
from tests.support import article, build_zim, free_port

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, sync_playwright

CHANNEL = "chrome"
READY_SECONDS = 20.0
ARTICLE_TEXT = "Photosynthesis turns light into chemical energy inside the chloroplast."


class Server:
    def __init__(self, root: Path) -> None:
        self.port = free_port()
        config = uvicorn.Config(
            create_app(root, ArcaConfig(auto_engine=False)),
            host="127.0.0.1",
            port=self.port,
            log_level="error",
        )
        self._server = uvicorn.Server(config)
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
def page(server: Server) -> Iterator[Page]:
    with sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(channel=CHANNEL, headless=True)
        except Exception as error:
            pytest.skip(f"no system chrome to drive: {error}")
        context = browser.new_context()
        opened = context.new_page()
        yield opened
        context.close()
        browser.close()


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
