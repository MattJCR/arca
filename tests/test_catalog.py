import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

from arca.catalog import (
    CatalogError,
    EntryNotFoundError,
    entries_url,
    fetch_categories,
    fetch_entries,
    fetch_languages,
    find_entry,
    format_size,
    http_get,
    parse_categories,
    parse_entries,
    parse_languages,
    parse_meta4,
    reader_url,
    record_zim_entry,
    resolve_downloads,
    safe_filename,
)
from arca.db import open_library_rw

FIXTURES = Path(__file__).parent / "fixtures"


def _feed() -> bytes:
    return (FIXTURES / "catalog_entries.xml").read_bytes()


def _meta4() -> bytes:
    return (FIXTURES / "sample.meta4").read_bytes()


def test_entries_url_includes_only_provided_filters() -> None:
    url = entries_url(query="wikipedia", lang="spa")
    assert url.startswith("https://library.kiwix.org/catalog/v2/entries?")
    assert "count=-1" in url
    assert "q=wikipedia" in url
    assert "lang=spa" in url
    assert "category" not in url


def test_parse_entries_extracts_the_catalog_fields() -> None:
    entries = parse_entries(_feed())
    assert len(entries) == 2
    wikipedia = entries[0]
    assert wikipedia.uuid == "0c45160e-f917-760a-9159-dfe3c53cdcdd"
    assert wikipedia.book_name == "wikipedia_es_all"
    assert wikipedia.filename == "wikipedia_es_all_maxi_2026-06.zim"
    assert wikipedia.title == "Wikipedia (Spanish)"
    assert wikipedia.language == "spa"
    assert wikipedia.flavour == "maxi"
    assert wikipedia.article_count == 1900000
    assert wikipedia.size_bytes == 112233445566
    assert wikipedia.has_fulltext is True
    assert wikipedia.meta4_url.endswith(".zim.meta4")


def test_the_reader_url_points_at_the_book_on_the_kiwix_library() -> None:
    wikipedia = parse_entries(_feed())[0]
    assert reader_url(wikipedia) == "https://library.kiwix.org/viewer#wikipedia_es_all"


def test_the_reader_url_escapes_the_book_name() -> None:
    wikipedia = parse_entries(_feed())[0]
    odd = replace(wikipedia, book_name="two words/and more")
    assert reader_url(odd).endswith("/viewer#two%20words/and%20more")


def test_a_book_without_a_name_gets_no_reader_url() -> None:
    wikipedia = parse_entries(_feed())[0]
    assert reader_url(replace(wikipedia, book_name="")) == ""


def test_parse_entries_skips_entries_without_acquisition_link() -> None:
    entries = parse_entries(_feed())
    assert all(entry.uuid != "no-acquisition-entry" for entry in entries)


def test_parse_entries_marks_missing_fulltext_index() -> None:
    devdocs = parse_entries(_feed())[1]
    assert devdocs.has_fulltext is False
    assert devdocs.flavour == ""


def test_parse_entries_rejects_invalid_xml() -> None:
    with pytest.raises(CatalogError):
        parse_entries(b"this is not xml")


def test_find_entry_raises_for_unknown_uuid() -> None:
    entries = parse_entries(_feed())
    assert find_entry(entries, "0c45160e-f917-760a-9159-dfe3c53cdcdd").book_name
    with pytest.raises(EntryNotFoundError):
        find_entry(entries, "missing-uuid")


def test_fetch_entries_wraps_network_errors() -> None:
    def failing_get(url: str) -> bytes:
        raise urllib.error.URLError("unreachable")

    with pytest.raises(CatalogError):
        fetch_entries("https://example.org/feed", failing_get)


def test_parse_meta4_orders_mirrors_by_priority() -> None:
    resolution = parse_meta4(_meta4())
    assert resolution.size_bytes == 112233445566
    assert resolution.sha256 == "0f2c71a0ec1cd0edaa4ba2f7cfa4dc02aa77c1efd279b1f28c05e2cf7a391a1e"
    assert resolution.hash_missing is False
    assert resolution.mirrors == (
        "https://mirror-one.example.org/zim/wikipedia_es_all_maxi_2026-06.zim",
        "https://mirror-two.example.org/zim/wikipedia_es_all_maxi_2026-06.zim",
        "https://mirror-last.example.org/zim/wikipedia_es_all_maxi_2026-06.zim",
    )


def test_resolve_downloads_appends_the_country_hint() -> None:
    seen: list[str] = []

    def get(url: str) -> bytes:
        seen.append(url)
        return _meta4()

    resolve_downloads("https://download.kiwix.org/x.zim.meta4", get)
    assert seen == ["https://download.kiwix.org/x.zim.meta4?country=ES"]


def test_resolve_downloads_degrades_when_the_meta4_is_missing() -> None:
    def get(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 404, "not found", None, None)  # type: ignore[arg-type]

    resolution = resolve_downloads("https://download.kiwix.org/x.zim.meta4", get)
    assert resolution.hash_missing is True
    assert resolution.sha256 == ""
    assert resolution.mirrors == ("https://download.kiwix.org/x.zim",)


def test_resolve_downloads_raises_when_the_network_is_down() -> None:
    def get(url: str) -> bytes:
        raise urllib.error.URLError("no route to host")

    with pytest.raises(CatalogError):
        resolve_downloads("https://download.kiwix.org/x.zim.meta4", get)


def test_record_zim_entry_upserts_without_touching_status(tmp_path: Path) -> None:
    connection = open_library_rw(tmp_path / "library.db")
    try:
        entry = parse_entries(_feed())[0]
        record_zim_entry(connection, entry)
        row = connection.execute("SELECT * FROM zim_files").fetchone()
        assert row["status"] == "missing"
        assert row["book_name"] == "wikipedia_es_all"
        connection.execute("UPDATE zim_files SET status = 'present' WHERE uuid = ?", (entry.uuid,))
        connection.commit()
        record_zim_entry(connection, entry)
        rows = connection.execute("SELECT status FROM zim_files").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "present"
    finally:
        connection.close()


def test_http_get_rejects_non_http_schemes() -> None:
    with pytest.raises(CatalogError):
        http_get("file:///C:/Windows/win.ini")
    with pytest.raises(CatalogError):
        http_get("ftp://example.org/feed")


def test_safe_filename_rejects_path_traversal() -> None:
    assert safe_filename("https://x.org/zim/good_2026-06.zim.meta4") == "good_2026-06.zim"
    assert safe_filename("https://x.org/zim/%2e%2e%2fescape.zim.meta4") == ""
    assert safe_filename("https://x.org/zim/dir%2ffile.zim.meta4") == ""
    assert safe_filename("https://x.org/zim/back%5cslash.zim.meta4") == ""
    assert safe_filename("https://x.org/zim/.meta4") == ""


def test_parse_entries_drops_entries_with_traversal_filenames() -> None:
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>urn:uuid:evil-entry</id>
        <title>Evil</title>
        <link rel="http://opds-spec.org/acquisition/open-access"
              type="application/x-zim"
              href="https://x.org/zim/%2e%2e%2f%2e%2e%2fboot.zim.meta4"/>
      </entry>
    </feed>
    """
    assert parse_entries(payload) == []


def test_parse_categories_returns_sorted_unique_titles() -> None:
    payload = (FIXTURES / "catalog_categories.xml").read_bytes()
    assert parse_categories(payload) == ["devdocs", "gutenberg", "wikipedia"]


def test_parse_languages_extracts_code_name_and_count() -> None:
    payload = (FIXTURES / "catalog_languages.xml").read_bytes()
    languages = parse_languages(payload)
    assert [language.code for language in languages] == ["eng", "spa", "und"]
    spanish = next(language for language in languages if language.code == "spa")
    assert spanish.name == "Spanish"
    assert spanish.book_count == 140
    unnamed = next(language for language in languages if language.code == "und")
    assert unnamed.name == "und"
    assert unnamed.book_count is None


def test_fetch_filters_wrap_network_errors() -> None:
    def failing(url: str) -> bytes:
        raise urllib.error.URLError("unreachable")

    with pytest.raises(CatalogError):
        fetch_categories(get=failing)
    with pytest.raises(CatalogError):
        fetch_languages(get=failing)


def test_format_size_covers_the_common_units() -> None:
    assert format_size(None) == "?"
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KiB"
    assert format_size(5 * 1024**3) == "5.0 GiB"
