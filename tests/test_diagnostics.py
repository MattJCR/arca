import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arca.config import ArcaConfig, config_path, init_library, library_db_path
from arca.db import open_library_rw
from arca.diagnostics import BUILD_EPOCH, CheckState, render, run_checks, worst_state
from arca.fetch import write_sha256_sidecar
from arca.i18n import load_catalog

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _states(root: Path, config: ArcaConfig, now: datetime) -> dict[str, CheckState]:
    return {result.check_id: result.state for result in run_checks(root, config, now=now)}


def test_healthy_library_reports_ok_or_honest_space(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    states = _states(root, ArcaConfig(), NOW)
    assert states["library_path"] is CheckState.OK
    assert states["write_access"] is CheckState.OK
    assert states["clock"] is CheckState.OK
    assert states["free_space"] in {CheckState.OK, CheckState.DEGRADED}


def test_missing_root_is_broken_and_still_checks_the_clock(tmp_path: Path) -> None:
    results = run_checks(tmp_path / "nowhere", ArcaConfig(), now=NOW)
    by_id = {result.check_id: result for result in results}
    assert set(by_id) == {"library_path", "clock"}
    assert by_id["library_path"].state is CheckState.BROKEN
    assert by_id["library_path"].params["root"]


def test_low_free_space_is_degraded(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    config = ArcaConfig(free_space_warn_gib=10.0**9)
    states = _states(root, config, NOW)
    assert states["free_space"] is CheckState.DEGRADED


def test_clock_before_build_epoch_is_broken(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    past = BUILD_EPOCH - timedelta(days=30)
    states = _states(root, ArcaConfig(), past)
    assert states["clock"] is CheckState.BROKEN


def test_files_from_the_future_degrade_the_clock(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    future = (NOW + timedelta(days=7)).timestamp()
    os.utime(config_path(root), (future, future))
    states = _states(root, ArcaConfig(), NOW)
    assert states["clock"] is CheckState.DEGRADED


def test_worst_state_prefers_broken_over_degraded(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    healthy = run_checks(root, ArcaConfig(), now=NOW)
    assert worst_state(healthy) in {CheckState.OK, CheckState.DEGRADED}
    broken = run_checks(tmp_path / "nowhere", ArcaConfig(), now=NOW)
    assert worst_state(broken) is CheckState.BROKEN


def test_render_produces_localized_text_in_both_languages(tmp_path: Path) -> None:
    broken = run_checks(tmp_path / "nowhere", ArcaConfig(), now=NOW)[0]
    spanish = render(broken, load_catalog("es"))
    english = render(broken, load_catalog("en"))
    assert spanish["state_label"] == "roto"
    assert english["state_label"] == "broken"
    assert str(tmp_path / "nowhere") in spanish["summary"]
    assert spanish["impact"] and spanish["action"]
    assert spanish["summary"] != english["summary"]


def test_render_omits_impact_and_action_when_ok(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    results = {result.check_id: result for result in run_checks(root, ArcaConfig(), now=NOW)}
    rendered = render(results["library_path"], load_catalog("es"))
    assert "impact" not in rendered
    assert "action" not in rendered


def test_content_checks_are_ok_on_a_clean_library(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    states = _states(root, ArcaConfig(), NOW)
    assert states["zim_integrity"] is CheckState.OK
    assert states["incomplete_downloads"] is CheckState.OK
    assert states["doc_drift"] is CheckState.OK
    assert states["undocumented_dependency"] is CheckState.OK


def test_zim_integrity_degrades_on_corrupt_missing_or_unverifiable(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    connection = open_library_rw(library_db_path(root))
    try:
        rows = (
            ("u-ok", "ok.zim", "present"),
            ("u-corrupt", "corrupt.zim", "corrupt"),
            ("u-missing", "missing.zim", "present"),
            ("u-nohash", "nohash.zim", "present"),
        )
        for uuid, filename, status in rows:
            connection.execute(
                "INSERT INTO zim_files (uuid, book_name, filename, status) VALUES (?, ?, ?, ?)",
                (uuid, filename, filename, status),
            )
        connection.commit()
    finally:
        connection.close()
    for filename in ("ok.zim", "corrupt.zim", "nohash.zim"):
        (root / "zim" / filename).write_bytes(b"payload")
    write_sha256_sidecar(root / "zim" / "ok.zim", "0" * 64)
    write_sha256_sidecar(root / "zim" / "corrupt.zim", "0" * 64)
    results = {result.check_id: result for result in run_checks(root, ArcaConfig(), now=NOW)}
    check = results["zim_integrity"]
    assert check.state is CheckState.DEGRADED
    assert check.params["corrupt"] == "1"
    assert check.params["missing"] == "1"
    assert check.params["unverifiable"] == "1"


def test_orphan_part_files_degrade_incomplete_downloads(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    orphan = root / "zim" / "orphan.zim.part"
    orphan.write_bytes(b"partial")
    results = {result.check_id: result for result in run_checks(root, ArcaConfig(), now=NOW)}
    assert results["incomplete_downloads"].state is CheckState.DEGRADED
    assert results["incomplete_downloads"].params["first"] == "orphan.zim.part"


def test_part_files_with_an_active_download_are_not_orphans(tmp_path: Path) -> None:
    root = tmp_path / "library"
    init_library(root)
    dest = root / "zim" / "active.zim"
    dest.with_name(dest.name + ".part").write_bytes(b"partial")
    connection = open_library_rw(library_db_path(root))
    try:
        connection.execute(
            "INSERT INTO downloads (kind, ref, filename, dest_path, status) "
            "VALUES ('zim', 'u1', 'active.zim', ?, 'paused')",
            (str(dest),),
        )
        connection.commit()
    finally:
        connection.close()
    states = _states(root, ArcaConfig(), NOW)
    assert states["incomplete_downloads"] is CheckState.OK


def test_documentation_drift_degrades_the_lock_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    drifted_lock = tmp_path / "sources.lock"
    drifted_lock.write_text(
        '[{"key": "fastapi", "version": "0.1.0", "kind": "pypi", "url": "", '
        '"commit": "", "sha256": "", "license": "MIT", "captured_at": "", "notes": ""}]',
        "utf-8",
    )
    monkeypatch.setattr("arca.diagnostics.default_lock_path", lambda: drifted_lock)
    results = {result.check_id: result for result in run_checks(root, ArcaConfig(), now=NOW)}
    assert results["doc_drift"].state is CheckState.DEGRADED
    assert "fastapi" in results["doc_drift"].params["detail"]
    assert results["undocumented_dependency"].state is CheckState.DEGRADED
    assert "numpy" in results["undocumented_dependency"].params["detail"]


def test_unreadable_lock_degrades_both_lock_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    init_library(root)
    monkeypatch.setattr("arca.diagnostics.default_lock_path", lambda: tmp_path / "missing.lock")
    states = _states(root, ArcaConfig(), NOW)
    assert states["doc_drift"] is CheckState.DEGRADED
    assert states["undocumented_dependency"] is CheckState.DEGRADED
