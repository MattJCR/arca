#!/usr/bin/env bash
# Hook: typecheck (PostToolUse on Edit|Write)
#
# Rule it automates: docs/development/typing.md and RNF-09 — strict mode, no
# implicit Any, no silenced warnings.
#
# Runs mypy on the edited file only. mypy still follows its imports, so the
# check stays meaningful, and its cache keeps repeated edits cheap.
#
# Justified bypass: SKIP_TYPECHECK_HOOK=1 — only for an intermediate edit that
# is known not to type-check yet and is completed in the same task.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

[[ "${SKIP_TYPECHECK_HOOK:-0}" == "1" ]] && exit 0

hook_field tool_input.file_path
FILE="${HOOK_FIELD_VALUE}"
[[ -z "${FILE}" ]] && exit 0
is_python_file "${FILE}" || exit 0
[[ -f "${FILE}" ]] || exit 0

require_python

if ! OUTPUT="$(cd "${PROJECT_DIR}" && "${PYTHON}" -m mypy "${FILE}" 2>&1)"; then
  hook_block "[typecheck] mypy rejects $(relative_path "${FILE}"):" "${OUTPUT}"
fi
