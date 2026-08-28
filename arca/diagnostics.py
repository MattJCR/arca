from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from arca.config import FREE_SPACE_FLOOR_GIB, ArcaConfig, config_path, library_db_path
from arca.i18n import Catalog

BUILD_EPOCH = datetime(2026, 8, 1, tzinfo=UTC)
FUTURE_TOLERANCE_SECONDS = 48 * 3600
GIB = 1024**3


class CheckState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    BROKEN = "broken"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    state: CheckState
    params: dict[str, str] = field(default_factory=dict)


def run_checks(root: Path, config: ArcaConfig, now: datetime | None = None) -> list[CheckResult]:
    moment = now if now is not None else datetime.now(UTC)
    path_check = _check_library_path(root)
    if path_check.state is CheckState.BROKEN:
        return [path_check, _check_clock(None, moment)]
    return [
        path_check,
        _check_free_space(root, config),
        _check_write_access(root),
        _check_clock(root, moment),
    ]


def worst_state(results: list[CheckResult]) -> CheckState:
    if any(result.state is CheckState.BROKEN for result in results):
        return CheckState.BROKEN
    if any(result.state is CheckState.DEGRADED for result in results):
        return CheckState.DEGRADED
    return CheckState.OK


def render(result: CheckResult, catalog: Catalog) -> dict[str, str]:
    base = f"diag.{result.check_id}.{result.state.value}"
    rendered = {
        "id": result.check_id,
        "state": result.state.value,
        "state_label": catalog.text(f"diag.state.{result.state.value}"),
        "summary": catalog.text(f"{base}.summary", **result.params),
    }
    for aspect in ("impact", "action"):
        key = f"{base}.{aspect}"
        if catalog.has(key):
            rendered[aspect] = catalog.text(key, **result.params)
    return rendered


def _check_library_path(root: Path) -> CheckResult:
    if root.is_dir() and os.access(root, os.R_OK):
        return CheckResult("library_path", CheckState.OK)
    return CheckResult("library_path", CheckState.BROKEN, {"root": str(root)})


def _check_free_space(root: Path, config: ArcaConfig) -> CheckResult:
    free_gib = shutil.disk_usage(root).free / GIB
    params = {"free_gib": f"{free_gib:.1f}", "warn_gib": f"{config.free_space_warn_gib:.1f}"}
    if free_gib < FREE_SPACE_FLOOR_GIB:
        return CheckResult("free_space", CheckState.BROKEN, params)
    if free_gib < config.free_space_warn_gib:
        return CheckResult("free_space", CheckState.DEGRADED, params)
    return CheckResult("free_space", CheckState.OK, params)


def _check_write_access(root: Path) -> CheckResult:
    if os.access(root, os.W_OK):
        return CheckResult("write_access", CheckState.OK)
    return CheckResult("write_access", CheckState.DEGRADED)


def _check_clock(root: Path | None, now: datetime) -> CheckResult:
    if now < BUILD_EPOCH:
        return CheckResult("clock", CheckState.BROKEN, {"now": now.date().isoformat()})
    newest = _newest_known_mtime(root)
    if newest is not None and (newest - now).total_seconds() > FUTURE_TOLERANCE_SECONDS:
        return CheckResult("clock", CheckState.DEGRADED, {"newest": newest.date().isoformat()})
    return CheckResult("clock", CheckState.OK)


def _newest_known_mtime(root: Path | None) -> datetime | None:
    if root is None or not root.is_dir():
        return None
    stamps = []
    for candidate in (config_path(root), library_db_path(root)):
        if candidate.is_file():
            stamps.append(datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC))
    if not stamps:
        return None
    return max(stamps)
