from __future__ import annotations

from pathlib import Path

import pytest

from arca.opds import (
    OpdsError,
    entry_fields,
    feed_url,
    http_get,
    parse_feed,
)

FIXTURES = Path(__file__).parent / "fixtures"
EPUB_MEDIA_TYPE = "application/epub+zip"


def _books() -> bytes:
    return (FIXTURES / "opds_books.xml").read_bytes()


def _kiwix() -> bytes:
    return (FIXTURES / "catalog_entries.xml").read_bytes()


def test_the_core_reads_any_atom_feed_not_only_the_kiwix_one() -> None:
    entries = parse_feed(_books())
    assert [entry.title for entry in entries] == [
        "Meditations",
        "The Republic",
        "A page, not a book",
    ]
    assert entries[0].entry_id == "aaaa1111-2222-3333-4444-555566667777"
    assert entries[0].summary.startswith("Marcus Aurelius")
    assert entries[0].language == "eng"


def test_the_core_keeps_every_link_with_its_relation_and_type() -> None:
    meditations = parse_feed(_books())[0]
    assert [link.media_type for link in meditations.links] == ["image/jpeg", EPUB_MEDIA_TYPE]
    assert meditations.links[1].length == 431200
    assert meditations.links[1].rel.endswith("/open-access")


def test_the_acquisition_link_is_chosen_by_media_type() -> None:
    meditations, republic, page = parse_feed(_books())
    chosen = meditations.acquisition((EPUB_MEDIA_TYPE,), ())
    assert chosen is not None
    assert chosen.href.endswith("meditations.epub")
    assert republic.acquisition((EPUB_MEDIA_TYPE,), ()) is not None
    assert page.acquisition((EPUB_MEDIA_TYPE,), ()) is None


def test_the_acquisition_link_can_be_chosen_by_suffix_when_the_type_is_missing() -> None:
    meditations = parse_feed(_books())[0]
    chosen = meditations.acquisition((), (".epub",))
    assert chosen is not None
    assert chosen.href.endswith("meditations.epub")


def test_without_a_hint_the_acquisition_relation_decides() -> None:
    meditations, _republic, page = parse_feed(_books())
    chosen = meditations.acquisition((), ())
    assert chosen is not None
    assert chosen.media_type == EPUB_MEDIA_TYPE
    assert page.acquisition((), ()) is None


def test_namespaced_fields_are_read_by_their_local_name() -> None:
    wikipedia = parse_feed(_kiwix())[0]
    assert wikipedia.fields["name"] == "wikipedia_es_all"
    assert wikipedia.fields["flavour"] == "maxi"
    assert wikipedia.fields["articleCount"] == "1900000"


def test_an_entry_without_links_still_parses() -> None:
    broken = parse_feed(_kiwix())[2]
    assert broken.entry_id == "no-acquisition-entry"
    assert broken.links == ()


def test_entry_fields_flattens_the_feeds_that_carry_no_links() -> None:
    payload = (FIXTURES / "catalog_languages.xml").read_bytes()
    fields = entry_fields(payload)
    assert {item["language"] for item in fields if item.get("language")} == {
        "eng",
        "spa",
        "und",
    }


def test_broken_xml_is_reported_as_an_opds_error() -> None:
    with pytest.raises(OpdsError):
        parse_feed(b"not xml at all")
    with pytest.raises(OpdsError):
        entry_fields(b"not xml at all")


def test_feed_url_leaves_the_path_alone_when_there_are_no_parameters() -> None:
    assert feed_url("https://x.org", "/catalog", []) == "https://x.org/catalog"
    assert feed_url("https://x.org", "/c", [("q", "a b")]) == "https://x.org/c?q=a+b"


def test_only_http_and_https_are_fetched() -> None:
    with pytest.raises(OpdsError):
        http_get("file:///C:/Windows/win.ini")
    with pytest.raises(OpdsError):
        http_get("ftp://example.org/feed")
