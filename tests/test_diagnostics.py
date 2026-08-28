import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arca.config import ArcaConfig, config_path, init_library
from arca.diagnostics import BUILD_EPOCH, CheckState, render, run_checks, worst_state
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
