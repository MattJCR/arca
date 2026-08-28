#!/usr/bin/env bash
# Hook: lint-and-format (PostToolUse on Edit|Write)
#
# Rule it automates: docs/development/coding-standards.md, through the single
# ruff configuration in pyproject.toml.
#
# Applies the formatter and the safe lint fixes to the file that was just
# edited, then blocks if anything is left that ruff cannot fix on its own.
# Non-Python files are none of its business.
#
# Justified bypass: SKIP_LINT_HOOK=1 — only when editing a file that ruff
# cannot parse yet (a deliberate syntax fixture, for instance).

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

[[ "${SKIP_LINT_HOOK:-0}" == "1" ]] && exit 0

hook_field tool_input.file_path
FILE="${HOOK_FIELD_VALUE}"
[[ -z "${FILE}" ]] && exit 0
is_python_file "${FILE}" || exit 0
[[ -f "${FILE}" ]] || exit 0

require_python

"${PYTHON}" -m ruff format --quiet "${FILE}" >/dev/null 2>&1
"${PYTHON}" -m ruff check --quiet --fix "${FILE}" >/dev/null 2>&1

if ! REMAINING="$("${PYTHON}" -m ruff check "${FILE}" 2>&1)"; then
  hook_block "[lint-and-format] ruff still reports problems in $(relative_path "${FILE}"):" \
    "${REMAINING}"
fi
