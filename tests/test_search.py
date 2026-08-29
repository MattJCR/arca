from __future__ import annotations

from pathlib import Path

from arca.search import (
    MODE_BOTH,
    MODE_SEMANTIC,
    SOURCE_ZIM,
    SearchResult,
    entry_url,
    interleave,
    normalize_limit,
    normalize_mode,
    search_library,
)
from arca.zim import (
    MAX_LIMIT,
    SEARCH_MODE_FULLTEXT,
    SEARCH_MODE_NONE,
    SEARCH_MODE_TITLE,
    ZimLibrary,
)
from tests.support import article, build_zim


def _collection(root: Path, name: str, title: str, bodies: dict[str, str], fulltext: bool) -> None:
    build_zim(
        root / "zim" / name,
        [article(path, path.removesuffix(".html").title(), body) for path, body in bodies.items()],
        title=title,
        main_path=next(iter(bodies)),
        fulltext=fulltext,
    )


def _library(tmp_path: Path) -> ZimLibrary:
    _collection(
        tmp_path,
        "alpha.zim",
        "Alpha",
        {"a1.html": "<p>marker one</p>", "a2.html": "<p>marker two</p>"},
        True,
    )
    _collection(
        tmp_path,
        "beta.zim",
        "Beta",
        {"b1.html": "<p>marker three</p>", "b2.html": "<p>marker four</p>"},
        True,
    )
    _collection(tmp_path, "gamma.zim", "Gamma", {"marker.html": "<p>unrelated</p>"}, False)
    return ZimLibrary(tmp_path)


def test_results_from_every_collection_are_labelled_with_their_origin(tmp_path: Path) -> None:
    library = _library(tmp_path)
    outcome = search_library(library, "marker")
    origins = {result.source_title for result in outcome.keyword}
    assert origins == {"Alpha", "Beta", "Gamma"}
    assert all(result.source_kind == SOURCE_ZIM for result in outcome.keyword)
    assert all(result.url.startswith("/zim/") for result in outcome.keyword)
    library.close()


def test_sources_are_interleaved_by_rank_not_merged_by_score(tmp_path: Path) -> None:
    library = _library(tmp_path)
    outcome = search_library(library, "marker")
    ranks = [result.rank for result in outcome.keyword]
    assert ranks == sorted(ranks)
    first_three = [result.source_title for result in outcome.keyword[:3]]
    assert len(set(first_three)) == 3
    library.close()


def test_each_source_reports_its_own_index_mode(tmp_path: Path) -> None:
    library = _library(tmp_path)
    outcome = search_library(library, "marker")
    modes = {report.title: report.mode for report in outcome.sources}
    assert modes == {
        "Alpha": SEARCH_MODE_FULLTEXT,
        "Beta": SEARCH_MODE_FULLTEXT,
        "Gamma": SEARCH_MODE_TITLE,
    }
    assert all(report.estimated_matches >= 1 for report in outcome.sources)
    library.close()


def test_scope_restricts_the_collections_searched(tmp_path: Path) -> None:
    library = _library(tmp_path)
    alpha = next(info for info in library.refresh() if info.title == "Alpha")
    outcome = search_library(library, "marker", scope=[alpha.uuid])
    assert {result.source_title for result in outcome.keyword} == {"Alpha"}
    assert [report.title for report in outcome.sources] == ["Alpha"]
    library.close()


def test_limit_caps_the_merged_list(tmp_path: Path) -> None:
    library = _library(tmp_path)
    outcome = search_library(library, "marker", limit=2)
    assert len(outcome.keyword) == 2
    library.close()


def test_empty_query_returns_no_results_but_still_lists_the_sources(tmp_path: Path) -> None:
    library = _library(tmp_path)
    outcome = search_library(library, "   ")
    assert outcome.keyword == []
    assert len(outcome.sources) == 3
    library.close()


def test_semantic_band_is_empty_and_declared_unavailable(tmp_path: Path) -> None:
    library = _library(tmp_path)
    outcome = search_library(library, "marker")
    assert outcome.semantic == []
    assert outcome.semantic_available is False
    semantic_only = search_library(library, "marker", mode=MODE_SEMANTIC)
    assert semantic_only.keyword == []
    assert semantic_only.mode == MODE_SEMANTIC
    library.close()


def test_a_collection_without_fulltext_still_reports_what_it_can_search(tmp_path: Path) -> None:
    build_zim(
        tmp_path / "zim" / "titles.zim",
        [article("only.html", "Only", "<p>text</p>")],
        title="Titles",
        main_path="only.html",
        fulltext=False,
    )
    library = ZimLibrary(tmp_path)
    outcome = search_library(library, "nothing here")
    assert [(report.title, report.mode) for report in outcome.sources] == [
        ("Titles", SEARCH_MODE_TITLE)
    ]
    assert outcome.keyword == []
    library.close()


def test_normalisers_fall_back_to_safe_defaults() -> None:
    assert normalize_mode("keyword") == "keyword"
    assert normalize_mode("nonsense") == MODE_BOTH
    assert normalize_mode(None) == MODE_BOTH
    assert normalize_limit(None) > 0
    assert normalize_limit(0) == 1
    assert normalize_limit(10_000) == MAX_LIMIT


def test_interleave_walks_rank_by_rank_across_groups() -> None:
    def result(source: str, rank: int) -> SearchResult:
        return SearchResult(SOURCE_ZIM, source, source, f"{source}{rank}", "p", "/zim/x/p", rank)

    groups = [
        [result("a", 0), result("a", 1), result("a", 2)],
        [result("b", 0)],
        [result("c", 0), result("c", 1)],
    ]
    assert [item.title for item in interleave(groups)] == ["a0", "b0", "c0", "a1", "c1", "a2"]
    assert interleave([]) == []


def test_entry_url_percent_encodes_the_path_but_keeps_its_folders() -> None:
    assert entry_url("u", "sub/dir/Teorema") == "/zim/u/sub/dir/Teorema"
    assert entry_url("u", "Mañana") == "/zim/u/Ma%C3%B1ana"


def test_every_source_reports_whether_it_could_answer(tmp_path: Path) -> None:
    library = _library(tmp_path)
    outcome = search_library(library, "marker")
    assert outcome.sources
    assert all(report.mode != SEARCH_MODE_NONE and not report.error for report in outcome.sources)
    library.close()
