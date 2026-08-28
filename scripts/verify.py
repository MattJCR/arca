from __future__ import annotations

import subprocess
import sys

CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff format", ("ruff", "format", "--check", ".")),
    ("ruff check", ("ruff", "check", ".")),
    ("mypy", ("mypy", "arca", "tests", "scripts")),
    ("pytest", ("pytest", "-q")),
)


def main() -> int:
    all_green = True
    for name, arguments in CHECKS:
        print(f"[verify] {name}")
        completed = subprocess.run([sys.executable, "-m", *arguments])
        if completed.returncode != 0:
            all_green = False
    print("[verify] green" if all_green else "[verify] red")
    return 0 if all_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
