from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from arca.zim import (
    SEARCH_MODE_FULLTEXT,
    SEARCH_MODE_NONE,
    SEARCH_MODE_TITLE,
    ZimEntryNotFoundError,
    ZimError,
    ZimLibrary,
    ZimNotFoundError,
    entry_directory,
    index_mode,
    inject_base,
    is_html,
    media_type,
    open_archive,
    read_entry,
    search_archive,
)
from tests.support import ZimArticle, article, build_zim, read_info

PLANTS = article("plants.html", "Plants", "<p>Plants use photosynthesis to make sugar.</p>")
MOUNTAINS = article("mountains.html", "Mountains", "<p>Orogeny lifts continental crust.</p>")
INDEX = article("index.html", "Main Page", "<p>Welcome to the sample archive.</p>")


def _sample(path: Path, *, fulltext: bool = True, title: str = "Sample") -> Path:
    return build_zim(
        path,
        [INDEX, PLANTS, MOUNTAINS],
        title=title,
        description="Sample archive",
        main_path="index.html",
        fulltext=fulltext,
        redirects=[("start.html", "Start", "index.html")],
    )


def test_read_info_reports_metadata_and_fulltext_index(tmp_path: Path) -> None:
    info = read_info(_sample(tmp_path / "zim" / "sample.zim"))
    assert info.title == "Sample"
    assert info.description == "Sample archive"
    assert info.language == "eng"
    assert info.filename == "sample.zim"
    assert info.uuid
    assert info.article_count == 3
    assert info.has_fulltext is True
    assert info.size_bytes > 0


def test_archive_without_fulltext_index_is_declared(tmp_path: Path) -> None:
    info = read_info(_sample(tmp_path / "zim" / "plain.zim", fulltext=False))
    assert info.has_fulltext is False


def test_fulltext_search_matches_body_terms(tmp_path: Path) -> None:
    path = _sample(tmp_path / "zim" / "sample.zim")
    archive = open_archive(path)
    info = read_info(path)
    results = search_archive(archive, info, "photosynthesis")
    assert results.mode == SEARCH_MODE_FULLTEXT
    assert results.estimated_matches >= 1
    assert [hit.path for hit in results.hits] == ["plants.html"]
    assert results.hits[0].title == "Plants"
    assert results.hits[0].source_title == "Sample"
    assert results.hits[0].rank == 0


def test_archive_without_fulltext_index_falls_back_to_titles(tmp_path: Path) -> None:
    path = _sample(tmp_path / "zim" / "plain.zim", fulltext=False)
    archive = open_archive(path)
    info = read_info(path)
    by_title = search_archive(archive, info, "Plants")
    assert by_title.mode == SEARCH_MODE_TITLE
    assert [hit.path for hit in by_title.hits] == ["plants.html"]
    body_only = search_archive(archive, info, "photosynthesis")
    assert body_only.hits == []


def test_empty_query_returns_no_hits(tmp_path: Path) -> None:
    path = _sample(tmp_path / "zim" / "sample.zim")
    results = search_archive(open_archive(path), read_info(path), "   ")
    assert results.estimated_matches == 0
    assert results.hits == []


def test_search_limit_and_offset_slice_the_result_set(tmp_path: Path) -> None:
    path = build_zim(
        tmp_path / "zim" / "many.zim",
        [
            article(f"a{index}.html", f"Article {index}", "<p>shared marker word</p>")
            for index in range(6)
        ],
        main_path="a0.html",
    )
    archive = open_archive(path)
    info = read_info(path)
    first = search_archive(archive, info, "marker", limit=2)
    second = search_archive(archive, info, "marker", limit=2, offset=2)
    assert len(first.hits) == 2
    assert len(second.hits) == 2
    assert second.hits[0].rank == 2
    assert not {hit.path for hit in first.hits} & {hit.path for hit in second.hits}


def test_read_entry_returns_content_and_mimetype(tmp_path: Path) -> None:
    archive = open_archive(_sample(tmp_path / "zim" / "sample.zim"))
    content = read_entry(archive, "plants.html")
    assert content.mimetype == "text/html"
    assert content.title == "Plants"
    assert b"photosynthesis" in content.data


def test_read_entry_follows_redirects(tmp_path: Path) -> None:
    path = _sample(tmp_path / "zim" / "sample.zim")
    archive = open_archive(path)
    info = read_info(path)
    assert info.main_path == "index.html"
    content = read_entry(archive, "start.html")
    assert content.path == "index.html"
    assert b"Welcome" in content.data


def test_read_entry_rejects_unknown_paths(tmp_path: Path) -> None:
    archive = open_archive(_sample(tmp_path / "zim" / "sample.zim"))
    with pytest.raises(ZimEntryNotFoundError):
        read_entry(archive, "missing.html")
    with pytest.raises(ZimEntryNotFoundError):
        read_entry(archive, "")


def test_open_archive_rejects_a_file_that_is_not_a_zim(tmp_path: Path) -> None:
    broken = tmp_path / "zim" / "broken.zim"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not a zim file")
    with pytest.raises(ZimError):
        open_archive(broken)


def test_library_lists_archives_and_resolves_by_uuid(tmp_path: Path) -> None:
    _sample(tmp_path / "zim" / "beta.zim", title="Beta")
    _sample(tmp_path / "zim" / "alpha.zim", title="Alpha")
    library = ZimLibrary(tmp_path)
    infos = library.refresh()
    assert [info.title for info in infos] == ["Alpha", "Beta"]
    first = infos[0]
    assert library.info(first.uuid).filename == "alpha.zim"
    assert library.content(first.uuid, "plants.html").title == "Plants"
    library.close()


def test_library_search_covers_every_archive(tmp_path: Path) -> None:
    _sample(tmp_path / "zim" / "one.zim", title="One")
    _sample(tmp_path / "zim" / "two.zim", title="Two")
    library = ZimLibrary(tmp_path)
    library.refresh()
    results = library.search_all("photosynthesis")
    assert sorted(result.source_title for result in results) == ["One", "Two"]
    assert all(result.hits[0].path == "plants.html" for result in results)
    library.close()


def test_library_refresh_tracks_added_and_removed_files(tmp_path: Path) -> None:
    _sample(tmp_path / "zim" / "one.zim", title="One")
    library = ZimLibrary(tmp_path)
    assert [info.title for info in library.refresh()] == ["One"]
    _sample(tmp_path / "zim" / "two.zim", title="Two")
    assert [info.title for info in library.refresh()] == ["One", "Two"]
    library.close()
    (tmp_path / "zim" / "one.zim").unlink()
    library = ZimLibrary(tmp_path)
    assert [info.title for info in library.refresh()] == ["Two"]
    library.close()


def test_library_reports_unreadable_archives_without_failing(tmp_path: Path) -> None:
    _sample(tmp_path / "zim" / "good.zim", title="Good")
    (tmp_path / "zim" / "bad.zim").write_bytes(b"garbage")
    library = ZimLibrary(tmp_path)
    infos = library.refresh()
    assert [info.title for info in infos] == ["Good"]
    assert [failure.filename for failure in library.failures()] == ["bad.zim"]
    library.close()


def test_library_without_directory_is_empty(tmp_path: Path) -> None:
    library = ZimLibrary(tmp_path)
    assert library.refresh() == []
    with pytest.raises(ZimNotFoundError):
        library.info("nope")


def test_entry_directory_keeps_only_the_parent_prefix() -> None:
    assert entry_directory("article.html") == ""
    assert entry_directory("sub/dir/article.html") == "sub/dir/"
    assert entry_directory("") == ""


def test_is_html_ignores_parameters_and_case() -> None:
    assert is_html("text/html")
    assert is_html("Text/HTML; charset=UTF-8")
    assert is_html("application/xhtml+xml")
    assert not is_html("image/png")


def test_media_type_adds_a_charset_only_to_text() -> None:
    assert media_type("text/html") == "text/html; charset=utf-8"
    assert media_type("image/svg+xml") == "image/svg+xml; charset=utf-8"
    assert media_type("image/png") == "image/png"
    assert media_type("text/html; charset=iso-8859-1") == "text/html; charset=iso-8859-1"
    assert media_type("") == "application/octet-stream"


def test_inject_base_puts_the_tag_right_after_the_head() -> None:
    result = inject_base(b"<html><head><title>x</title></head></html>", "/zim/u/")
    assert result == b'<html><head><base href="/zim/u/"><title>x</title></head></html>'


def test_inject_base_replaces_an_existing_base() -> None:
    result = inject_base(b'<head><base href="../"><title>x</title></head>', "/zim/u/sub/")
    assert result.count(b"<base") == 1
    assert b'<base href="/zim/u/sub/">' in result


def test_inject_base_handles_documents_without_a_head() -> None:
    result = inject_base(b"<p>bare</p>", "/zim/u/")
    assert result.startswith(b'<base href="/zim/u/">')


def test_inject_base_escapes_the_href() -> None:
    result = inject_base(b"<head></head>", '/zim/u"onload="x/')
    assert b'"onload=' not in result.split(b"<base", 1)[1].split(b">", 1)[0].replace(b"&quot;", b"")


def test_served_articles_carry_a_base_pointing_at_their_own_folder(tmp_path: Path) -> None:
    path = build_zim(
        tmp_path / "zim" / "nested.zim",
        [
            ZimArticle("sub/dir/page.html", "Page", "<html><head></head><body>x</body></html>"),
        ],
        main_path="sub/dir/page.html",
    )
    archive = open_archive(path)
    content = read_entry(archive, "sub/dir/page.html")
    assert entry_directory(content.path) == "sub/dir/"


def test_release_frees_the_file_so_it_can_be_replaced_in_place(tmp_path: Path) -> None:
    path = _sample(tmp_path / "zim" / "sample.zim", title="Before")
    library = ZimLibrary(tmp_path)
    assert [info.title for info in library.refresh()] == ["Before"]
    replacement = build_zim(
        tmp_path / "incoming.zim",
        [article("page.html", "Page", "<p>new</p>")],
        title="After",
        main_path="page.html",
    )
    assert library.release("sample.zim") is True
    assert library.release("sample.zim") is False
    os.replace(replacement, path)
    assert [info.title for info in library.refresh()] == ["After"]
    library.close()


def test_search_declares_a_collection_with_no_index_at_all(tmp_path: Path) -> None:
    path = _sample(tmp_path / "zim" / "sample.zim")
    archive = open_archive(path)
    info = replace(read_info(path), has_fulltext=False, has_title_index=False)
    assert index_mode(info) == SEARCH_MODE_NONE
    results = search_archive(archive, info, "photosynthesis")
    assert results.mode == SEARCH_MODE_NONE
    assert results.estimated_matches == 0
    assert results.hits == []


def test_title_falls_back_to_the_file_name_when_the_zim_has_no_title(tmp_path: Path) -> None:
    path = build_zim(
        tmp_path / "zim" / "untitled-collection.zim",
        [article("page.html", "Page", "<p>text</p>")],
        title="",
        main_path="page.html",
    )
    assert read_info(path).title == "untitled-collection"
