from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from arca.opds import HttpGet
from arca.sources import (
    FILTER_CATEGORY,
    FILTER_FLAVOUR,
    FILTER_LANGUAGE,
    FILTER_QUERY,
    ITEM_BOOK,
    ITEM_ZIM,
    KIND_JSON,
    KIND_MANUAL,
    KIND_OPDS,
    STATUS_DOWN,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_UNVERIFIED,
    Source,
    SourceError,
    SourceRegistry,
    default_sources,
    filename_from_url,
    find_item,
    parse_sources,
    resolve,
    search,
    source_states,
    state_payload,
)

FIXTURES = Path(__file__).parent / "fixtures"
EPUB_MEDIA_TYPE = "application/epub+zip"


def _books_source(language_param: str = "") -> Source:
    return Source(
        source_id="books",
        name="A books catalogue",
        kind=KIND_OPDS,
        base_url="https://books.example.org",
        entries_path="/opds",
        media_type=EPUB_MEDIA_TYPE,
        item_kind=ITEM_BOOK,
        language_param=language_param,
    )


def _serve(payload: bytes, seen: list[str] | None = None) -> HttpGet:
    def get(url: str) -> bytes:
        if seen is not None:
            seen.append(url)
        return payload

    return get


def test_without_configuration_the_only_source_is_the_kiwix_one() -> None:
    sources = default_sources()
    assert [source.source_id for source in sources] == ["kiwix"]
    assert sources[0].item_kind == ITEM_ZIM
    assert sources[0].verifies_hash is True
    assert parse_sources(None) == sources
    assert parse_sources([]) == sources


def test_a_source_declared_in_the_configuration_needs_no_code() -> None:
    sources = parse_sources(
        [
            {
                "source_id": "standard-ebooks",
                "name": "Standard Ebooks",
                "kind": "opds",
                "base_url": "https://standardebooks.example",
                "entries_path": "/feeds/opds/all",
                "media_type": EPUB_MEDIA_TYPE,
                "item_kind": "book",
            }
        ]
    )
    assert len(sources) == 1
    source = sources[0]
    assert source.name == "Standard Ebooks"
    assert source.item_kind == ITEM_BOOK
    assert source.verifies_hash is False
    assert source.filters == (FILTER_QUERY,)


def test_a_broken_declaration_is_dropped_instead_of_breaking_the_start() -> None:
    sources = parse_sources(
        [
            {"source_id": "", "kind": "opds", "base_url": "https://x.org"},
            {"source_id": "no-url", "kind": "opds"},
            {"source_id": "bad kind", "kind": "torrent", "base_url": "https://x.org"},
            {"source_id": "../escape", "kind": "opds", "base_url": "https://x.org"},
            "not an object",
        ]
    )
    assert [source.source_id for source in sources] == ["kiwix"]


def test_the_same_identifier_declared_twice_keeps_the_first() -> None:
    sources = parse_sources(
        [
            {"source_id": "books", "name": "First", "base_url": "https://one.example"},
            {"source_id": "books", "name": "Second", "base_url": "https://two.example"},
        ]
    )
    assert [source.name for source in sources] == ["First"]


def test_a_kiwix_source_is_always_a_zim_source() -> None:
    sources = parse_sources(
        [
            {
                "source_id": "mirror",
                "kind": "opds",
                "flavour": "kiwix",
                "base_url": "https://mirror.example",
                "item_kind": "book",
            }
        ]
    )
    assert sources[0].item_kind == ITEM_ZIM
    assert sources[0].filters == (
        FILTER_QUERY,
        FILTER_LANGUAGE,
        FILTER_CATEGORY,
        FILTER_FLAVOUR,
    )


def test_a_plain_opds_source_yields_items_with_their_download_url() -> None:
    payload = (FIXTURES / "opds_books.xml").read_bytes()
    seen: list[str] = []
    items = search(_books_source(), query="plato", get=_serve(payload, seen))
    assert seen == ["https://books.example.org/opds?q=plato"]
    assert [item.title for item in items] == ["Meditations", "The Republic"]
    assert items[0].filename == "meditations.epub"
    assert items[0].url.endswith("/meditations.epub")
    assert items[0].size_bytes == 431200
    assert items[0].ref == "books:aaaa1111-2222-3333-4444-555566667777"


def test_a_source_that_declares_no_language_filter_never_sends_one() -> None:
    payload = (FIXTURES / "opds_books.xml").read_bytes()
    seen: list[str] = []
    search(_books_source(), query="a", language="eng", get=_serve(payload, seen))
    assert seen == ["https://books.example.org/opds?q=a"]
    seen.clear()
    source = _books_source("lang")
    search(source, query="a", language="eng", get=_serve(payload, seen))
    assert seen == ["https://books.example.org/opds?q=a&lang=eng"]
    assert source.filters == (FILTER_QUERY, FILTER_LANGUAGE)


def test_a_json_source_maps_its_own_field_names() -> None:
    document = json.dumps(
        {
            "results": [
                {
                    "slug": "anatomy",
                    "name": "Anatomy and Physiology",
                    "href": "https://json.example/files/anatomy.pdf",
                    "digest": "a" * 64,
                    "bytes": 4096,
                },
                {"name": "no url here"},
            ]
        }
    ).encode("utf-8")
    source = Source(
        source_id="openstax",
        name="OpenStax",
        kind=KIND_JSON,
        base_url="https://json.example",
        entries_path="/api/books",
        items_key="results",
        fields={
            "id": "slug",
            "title": "name",
            "url": "href",
            "sha256": "digest",
            "size": "bytes",
        },
    )
    items = search(source, query="anatomy", get=_serve(document))
    assert len(items) == 1
    assert items[0].item_id == "anatomy"
    assert items[0].title == "Anatomy and Physiology"
    assert items[0].filename == "anatomy.pdf"
    assert items[0].sha256 == "a" * 64
    assert items[0].size_bytes == 4096
    assert source.verifies_hash is True


def test_a_json_document_that_is_not_the_promised_shape_is_reported() -> None:
    source = Source(
        source_id="broken",
        name="Broken",
        kind=KIND_JSON,
        base_url="https://json.example",
        items_key="results",
    )
    with pytest.raises(SourceError):
        search(source, get=_serve(b"{"))
    with pytest.raises(SourceError):
        search(source, get=_serve(b'{"results": 3}'))


def test_a_manual_source_is_never_searched() -> None:
    source = Source(source_id="manual", name="By hand", kind=KIND_MANUAL)
    assert source.searchable is False
    assert source.filters == ()
    assert search(source) == []


def test_the_network_failure_of_one_source_is_a_source_error() -> None:
    def broken(url: str) -> bytes:
        raise urllib.error.URLError("no route to host")

    with pytest.raises(SourceError):
        search(_books_source(), get=broken)


def test_a_download_without_a_hash_is_declared_unverified() -> None:
    payload = (FIXTURES / "opds_books.xml").read_bytes()
    source = _books_source()
    items = search(source, get=_serve(payload))
    resolution = resolve(source, items[0])
    assert resolution.mirrors == ("https://books.example.org/ebooks/meditations.epub",)
    assert resolution.hash_missing is True
    assert resolution.sha256 == ""


def test_a_kiwix_download_still_goes_through_the_metalink() -> None:
    entries = (FIXTURES / "catalog_entries.xml").read_bytes()
    meta4 = (FIXTURES / "sample.meta4").read_bytes()
    source = default_sources()[0]
    items = search(source, query="wikipedia", get=_serve(entries))
    assert items[0].item_kind == ITEM_ZIM
    assert items[0].reader_url.endswith("#wikipedia_es_all_maxi_2026-06")
    resolution = resolve(source, items[0], _serve(meta4))
    assert resolution.hash_missing is False
    assert len(resolution.mirrors) == 3


def test_find_item_picks_the_entry_the_operator_asked_for() -> None:
    payload = (FIXTURES / "opds_books.xml").read_bytes()
    items = search(_books_source(), get=_serve(payload))
    assert find_item(items, items[1].item_id) == items[1]
    assert find_item(items, "nothing") is None


def test_filename_from_url_refuses_to_escape_the_destination() -> None:
    assert filename_from_url("https://x.org/a/b/good.epub") == "good.epub"
    assert filename_from_url("https://x.org/a/good.epub?token=1") == "good.epub"
    assert filename_from_url("https://x.org/a/%2e%2e%2fescape.epub") == ""
    assert filename_from_url("https://x.org/a/dir%2ffile.epub") == ""
    assert filename_from_url("https://x.org/a/") == ""


def test_a_source_starts_unknown_and_becomes_what_was_observed() -> None:
    registry = SourceRegistry(parse_sources([{"source_id": "books", "base_url": "https://x.org"}]))
    assert [state.status for state in registry.states()] == [STATUS_UNKNOWN]
    registry.observe("books", False, "no route to host")
    down = registry.states()[0]
    assert down.status == STATUS_DOWN
    assert down.detail == "no route to host"
    assert down.checked_at
    registry.observe("books", True)
    assert registry.states()[0].status == STATUS_UNVERIFIED


def test_a_source_that_verifies_hashes_reports_plain_ok() -> None:
    registry = SourceRegistry(default_sources())
    registry.observe("kiwix", True)
    assert registry.states()[0].status == STATUS_OK


def test_replacing_the_declaration_forgets_what_was_observed_of_the_gone_ones() -> None:
    registry = SourceRegistry(parse_sources([{"source_id": "books", "base_url": "https://x.org"}]))
    registry.observe("books", True)
    registry.replace(default_sources())
    assert [state.source_id for state in registry.states()] == ["kiwix"]
    assert registry.states()[0].status == STATUS_UNKNOWN
    assert registry.get("books") is None
    assert registry.get("kiwix") is not None


def test_only_the_enabled_sources_are_searched() -> None:
    registry = SourceRegistry(
        parse_sources(
            [
                {"source_id": "books", "base_url": "https://x.org"},
                {"source_id": "off", "base_url": "https://y.org", "enabled": False},
            ]
        )
    )
    assert [source.source_id for source in registry.enabled()] == ["books"]
    assert len(registry.all()) == 2


def test_the_state_of_every_source_can_be_read_without_touching_the_network() -> None:
    states = source_states(parse_sources([{"source_id": "books", "base_url": "https://x.org"}]))
    payload = state_payload(states[0])
    assert payload["status"] == STATUS_UNKNOWN
    assert payload["verifies_hash"] is False
    assert payload["filters"] == [FILTER_QUERY]
    assert payload["base_url"] == "https://x.org"


def test_a_manual_source_is_declared_without_any_address() -> None:
    sources = parse_sources([{"source_id": "byhand", "name": "A mano", "kind": "manual"}])
    assert len(sources) == 1
    assert sources[0].kind == KIND_MANUAL
    assert sources[0].base_url == ""
    assert sources[0].searchable is False
    assert state_payload(source_states(sources)[0])["searchable"] is False
